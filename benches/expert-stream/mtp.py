"""Inkling 2 Mini with experts streamed from disk AND its bundled draft head (2026-11-27, user: "also try the MTP draft
head"). Speed only, greedy.

The draft head (DSpark: a 3-layer block drafter with a Markov head, block 8) reads the target's hidden state at four
layer visits (5, 23, 41 and the final one), drafts up to --draft tokens, and the target checks them all in one forward
pass. With experts on the SSD that matters twice: one pass reads each expert once for several tokens.

The drafter is ported from MTPLX 2.13.3 (mtplx/models/inkling/draft.py, Apache 2.0), without its Metal fast paths. The
target is the mlx-lm port with stream.py's StreamedExperts. Rejected drafts: the attention caches are trimmed and the
convolution states restored from a snapshot, then the accepted tokens run again (exact, costs a second pass).

    ~/Code/mlx-lm-inkling2/.venv/bin/python benches/expert-stream/mtp.py --slots 32 --draft 3 --ref results/expert-stream-ref.npz
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import mlx.core as mx
import mlx.nn as nn
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import stream  # noqa: E402
from mlx_lm.models.cache import ArraysCache, KVCache  # noqa: E402
from mlx_lm.models.inkling2.layers import unweighted_rms_norm  # noqa: E402


def linear(x, w):
    return x @ w.T


class DraftBlock:
    def __init__(self, w, cfg, index):
        self.w, self.cfg = w, cfg
        self.window = cfg["sliding_window_size"] if index in cfg["local_layer_ids"] else None

    def normalize(self, x, name):
        return mx.fast.rms_norm(x, self.w[name + ".weight"], self.cfg["rms_norm_eps"])

    def heads(self, x, name):
        return linear(x, self.w["self_attn." + name + "_proj.weight"]).reshape(
            *x.shape[:2], -1, self.cfg["head_dim"]).transpose(0, 2, 1, 3)

    def rotate(self, x, pos):
        return mx.fast.rope(x, self.cfg["head_dim"], traditional=False, base=self.cfg["rope_theta"], scale=1.0,
                            offset=pos)

    def observe(self, features, entry):
        pos = entry.offset
        if self.window and features.shape[1] > self.window:
            skip = features.shape[1] - self.window
            pos += skip
            entry.offset += skip
            features = features[:, -self.window:]
        keys = self.normalize(self.heads(features, "k_ctx"), "self_attn.k_norm_ctx")
        entry.update_and_fetch(self.rotate(keys, pos), self.heads(features, "v_ctx"))

    def attend(self, x, entry):
        key, value = entry.keys_and_values()  # the committed context (this mlx-lm's state also carries the offset)
        if self.window:
            key, value = key[:, :, -self.window:], value[:, :, -self.window:]
        q = self.rotate(self.normalize(self.heads(x, "q"), "self_attn.q_norm"), entry.offset)
        k = self.rotate(self.normalize(self.heads(x, "k"), "self_attn.k_norm"), entry.offset)
        out = mx.fast.scaled_dot_product_attention(q, mx.concatenate([key, k], axis=2),
                                                   mx.concatenate([value, self.heads(x, "v")], axis=2),
                                                   scale=self.cfg["head_dim"] ** -0.5)
        return linear(out.transpose(0, 2, 1, 3).reshape(*x.shape[:2], -1), self.w["self_attn.o_proj.weight"])

    def __call__(self, hidden, residual, entry):
        summed = hidden if residual is None else hidden.astype(mx.float32) + residual.astype(mx.float32)
        attn = self.attend(self.normalize(summed, "input_layernorm").astype(hidden.dtype), entry)
        summed = summed.astype(hidden.dtype).astype(mx.float32) + attn.astype(mx.float32)
        x = self.normalize(summed, "post_attention_layernorm").astype(hidden.dtype)
        gate = linear(x, self.w["mlp.gate_proj.weight"]).astype(mx.float32)
        up = linear(x, self.w["mlp.up_proj.weight"]).astype(mx.float32)
        return linear((nn.silu(gate) * up).astype(x.dtype), self.w["mlp.down_proj.weight"]), summed.astype(hidden.dtype)


class Drafter:
    def __init__(self, weights, cfg, unembed, embed, vocab):
        fuse = weights.pop("fuse_weight")
        self.cfg, self.unembed, self.embed, self.vocab = cfg, unembed, embed, vocab
        self.blend = mx.softmax(fuse.astype(mx.float32), axis=-1).astype(fuse.dtype)
        self.mask = weights.pop("mask_embed")
        self.output_norm = weights.pop("norm.weight")
        self.blocks = []
        for i in range(cfg["num_hidden_layers"]):
            stem = f"layers.{i}."
            local = {k[len(stem):]: weights.pop(k) for k in tuple(weights) if k.startswith(stem)}
            for name in ("k", "v"):
                local[f"self_attn.{name}_ctx_proj.weight"] = local.pop(f"self_attn.{name}_proj_ctx.weight")
            self.blocks.append(DraftBlock(local, cfg, i))
        self.markov = {k.removeprefix("markov_head."): weights.pop(k) for k in tuple(weights)
                       if k.startswith("markov_head.")}
        assert not weights, sorted(weights)
        self.width = cfg["block_size"]
        self.folded = None

    def make_cache(self):
        return [KVCache() for _ in self.blocks] + [ArraysCache(1)]

    def mix(self, features, row, weight):
        mixed = sum(h.astype(mx.float32) * self.blend[row, i].astype(mx.float32) for i, h in enumerate(features))
        return mx.fast.rms_norm(mixed, weight, self.cfg["rms_norm_eps"]).astype(features[0].dtype)

    def observe(self, captures, cache):
        for row, block in enumerate(self.blocks):
            block.observe(self.mix(captures, row, block.w["dflare_norm.weight"]), cache[row])
        cache[-1][0] = self.mix([h[:, -1:] for h in captures], -1, self.markov["dflare_norm.weight"])

    def fold(self):
        if self.folded is None:
            w, b = self.markov["hidden_out.weight"], self.markov["hidden_out.bias"]
            projection = mx.concatenate([w, b[:, None]], axis=1).astype(mx.float32)
            tiles = []
            for first in range(0, self.vocab, 4096):
                tile = (self.unembed[first:min(first + 4096, self.vocab)].astype(mx.float32) @ projection).astype(w.dtype)
                mx.eval(tile)
                tiles.append(tile)
            self.folded = mx.concatenate(tiles)
            mx.eval(self.folded)
        return self.folded

    def propose(self, anchor, cache, count):
        assert 1 <= count < self.width
        embed = self.embed(mx.array([[anchor]]))
        dtype = embed.dtype
        masks = mx.broadcast_to(self.mask, (1, self.width - 1, embed.shape[-1]))
        h, residual = mx.concatenate([embed, masks], axis=1), None
        for i, block in enumerate(self.blocks):
            h, residual = block(h, residual, cache[i])
        h = mx.fast.rms_norm(h.astype(mx.float32) + residual.astype(mx.float32), self.output_norm,
                             self.cfg["rms_norm_eps"]).astype(dtype)[:, 1:count + 1]
        base = linear(h, self.unembed)[..., :self.vocab]
        rank = self.cfg["markov_rank"]
        weights = self.markov["rnn_proj.weight"].astype(mx.float32)
        inputs = mx.concatenate([h, mx.broadcast_to(cache[-1][0], h.shape)], axis=-1)
        conditioned = linear(inputs.astype(mx.float32), weights[:, 2 * rank:]) + \
            self.markov["rnn_proj.bias"].astype(mx.float32)
        folded = self.fold()
        state, prev = mx.zeros((1, rank), dtype), mx.array([anchor])
        guesses = []
        for i in range(count):
            inputs = mx.concatenate([state, self.markov["markov_w1.weight"][prev]], axis=-1)
            update = (linear(inputs.astype(mx.float32), weights[:, :2 * rank]) + conditioned[:, i]).astype(dtype)
            gate, candidate, output = mx.split(update, 3, axis=-1)
            gate = mx.sigmoid(gate.astype(mx.float32)).astype(dtype)
            state = gate * state + (1 - gate) * mx.tanh(candidate.astype(mx.float32)).astype(dtype)
            signal = mx.tanh(output.astype(mx.float32)).astype(dtype)
            scores = base[:, i] + mx.addmm(folded[:, -1], signal, folded[:, :-1].T)
            prev = mx.argmax(scores, axis=-1)
            guesses.append(prev)
        return [int(t) for t in np.array(mx.stack(guesses, axis=-1)[0])]


def target_forward(model, tokens, cache, capture_ids):
    """The port's forward with the draft head's taps: the residual input of the visits in capture_ids, and the final
    hidden state (visit 48). Returns softcapped logits for every position and the captures."""
    lm = model.model.language_model
    core = lm.args.loop_core
    hidden_states = lm.embed(mx.array([tokens]))
    conv_mask = cache[0][1].make_mask(hidden_states.shape[1])
    caches, captured, visit = iter(cache), {}, [0]

    def run(layers, hidden_states, block_states, bank=None):
        for i, layer in enumerate(layers):
            if visit[0] in capture_ids:
                captured[visit[0]] = hidden_states
            messages = None if bank is None else lm.message_input(bank, i)
            hidden_states, block_states = layer(hidden_states, block_states, cache=next(caches), conv_mask=conv_mask,
                                                messages=messages)
            visit[0] += 1
        return hidden_states, block_states

    prelude = run(lm.layers[:core.start], hidden_states, None)
    hidden_states, block_states = prelude
    for loop_pass in range(lm.args.loop_config["num_passes"]):
        bank = None
        if loop_pass > 0:
            bank = (block_states[prelude[1].shape[0] + 1:], unweighted_rms_norm(hidden_states.astype(mx.float32)))
            hidden_states, block_states = prelude
        hidden_states, block_states = run(lm.layers[core.start:core.stop], hidden_states, block_states, bank)
    hidden_states, block_states = run(lm.layers[core.stop:], hidden_states, block_states)
    normed = unweighted_rms_norm(hidden_states.astype(mx.float32))
    hidden = lm.norm(lm.final_input(block_states, normed)[..., 0, :]).astype(hidden_states.dtype)
    if visit[0] in capture_ids:
        captured[visit[0]] = hidden
    text = model.args.text
    logits = model.lm_head(hidden).astype(mx.float32)
    logits = (mx.tanh(logits / text.final_logit_softcapping) * text.final_logit_softcapping)[..., :text.unpadded_vocab_size]
    return logits, [captured[i] for i in capture_ids]


def conv_snapshot(cache):
    return [list(c[1].cache) for c in cache]


def conv_restore(cache, snap):
    for c, s in zip(cache, snap):
        c[1].cache = list(s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, default=32)
    ap.add_argument("--resident", action="store_true")
    ap.add_argument("--loop-weighted", action="store_true")
    ap.add_argument("--policy", choices=("lru", "lfu"), default="lru")
    ap.add_argument("--prefetch", action="store_true")
    ap.add_argument("--draft", type=int, default=3, help="tokens drafted per round (1-7)")
    ap.add_argument("--tokens", type=int, default=256)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--ref", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    t0 = time.perf_counter()
    model, layers, stats, dense_gb, plan = stream.load(a)
    config = json.loads(stream.blob(layers["config.json"]["digest"]).read_text())
    mcfg = config["mtp_config"]
    dweights = {}
    for name, l in layers.items():
        if name.startswith("draft.") and not name.endswith(".json"):
            dweights.update({k.removeprefix("draft."): v for k, v in
                             mx.load(str(stream.blob(l["digest"])), format="safetensors").items()})
    lm = model.model.language_model
    drafter = Drafter(dweights, mcfg, model.lm_head.weight, lm.embed, model.args.text.unpadded_vocab_size)
    mx.eval([b.w for b in drafter.blocks], drafter.markov, drafter.mask, drafter.blend, drafter.output_norm)
    drafter.fold()
    load_s = time.perf_counter() - t0
    tok = stream.tokenizer(layers, Path(__file__).parent / ".tokenizer")
    ids = tok.encode(stream.PROMPT)
    capture_ids = tuple(mcfg["target_layer_ids"])

    cache, dcache = model.make_cache(), drafter.make_cache()
    mx.reset_peak_memory()
    t = time.perf_counter()
    logits, caps = target_forward(model, ids, cache, capture_ids)
    drafter.observe(caps, dcache)
    anchor = int(mx.argmax(logits[0, -1]).item())
    mx.eval(dcache[-1][0])
    prefill_s = time.perf_counter() - t
    after_prefill = stats.snap()

    out = [anchor]
    rounds = reruns = accepted_total = 0
    t_draft = t_verify = t_rerun = 0.0
    t_dec = time.perf_counter()
    while len(out) < a.tokens:
        k = min(a.draft, a.tokens - len(out))
        t = time.perf_counter()
        guesses = drafter.propose(anchor, dcache, k) if k >= 1 else []
        t_draft += time.perf_counter() - t
        t = time.perf_counter()
        snap = conv_snapshot(cache)
        inputs = [anchor] + guesses
        logits, caps = target_forward(model, inputs, cache, capture_ids)
        picks = [int(x) for x in np.array(mx.argmax(logits[0], axis=-1))]
        t_verify += time.perf_counter() - t
        n_ok = 0
        while n_ok < len(guesses) and guesses[n_ok] == picks[n_ok]:
            n_ok += 1
        rounds += 1
        accepted_total += n_ok
        if n_ok < len(guesses):  # roll back to the accepted inputs and run them again
            t = time.perf_counter()
            for c in cache:
                c[0].trim(len(inputs))
            conv_restore(cache, snap)
            reruns += 1
            _, caps = target_forward(model, inputs[:n_ok + 1], cache, capture_ids)
            mx.eval(caps)
            t_rerun += time.perf_counter() - t
        drafter.observe(caps, dcache)
        new = guesses[:n_ok] + [picks[n_ok]]
        out.extend(new)
        anchor = new[-1]
    mx.eval(dcache[-1][0])
    dec_s = time.perf_counter() - t_dec
    out = out[:a.tokens]
    s = stats.snap()
    dec = {k: s[k] - after_prefill[k] for k in s}
    n = len(out) - 1
    res = dict(
        mode="mtp", slots=a.slots, loop_weighted=a.loop_weighted, policy=a.policy, resident=a.resident, draft=a.draft,
        slot_plan=plan, tokens=a.tokens, prompt_tokens=len(ids), dense_gb=round(dense_gb, 2),
        peak_gb=round(mx.get_peak_memory() / 1e9, 2), load_s=round(load_s, 1),
        prefill_tok_s=round(len(ids) / prefill_s, 1), decode_tok_s=round(n / dec_s, 2),
        rounds=rounds, tokens_per_round=round(n / rounds, 2), accept_rate=round(accepted_total / (rounds * a.draft), 3),
        rerun_share=round(reruns / rounds, 3),
        ms_per_round=dict(draft=round(t_draft / rounds * 1000, 1), verify=round(t_verify / rounds * 1000, 1),
                          rerun=round(t_rerun / rounds * 1000, 1)),
        ms_read_per_token=round(dec["read_s"] / n * 1000, 1), ms_router_wait_per_token=round(dec["wait_s"] / n * 1000, 1),
        hit_rate=round(dec["hits"] / max(dec["lookups"], 1), 3), mb_read_per_token=round(dec["bytes"] / 1e6 / n, 1),
        text=tok.decode(out)[:300],
    )
    if a.ref:
        ref = [int(x) for x in np.load(a.ref)["tokens"]]
        same = [x == y for x, y in zip(out, ref)]
        res["ref_first_token_mismatch"] = None if all(same) else same.index(False)
    line = json.dumps(res)
    print(line)
    if a.out:
        with open(a.out, "a") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())
