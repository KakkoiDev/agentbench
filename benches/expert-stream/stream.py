"""Inkling 2 Mini on MLX with its routed experts streamed from disk: decode speed for smaller Macs (2026-11-27).

Inkling 2 Mini is 18.9 GB, of which 15.7 GB are the routed experts (128 per layer, 8 used per token). Only the 3.2 GB
of shared weights (attention, router, embeddings) stay in memory. Each layer keeps a cache of expert slots; a token
whose router picks an expert outside the cache reads it from the Ollama blob on the SSD into a slot. Reads bypass the
macOS file cache (F_NOCACHE), so a 32 GB Mac measures what a small one would see.

Options that change speed (memory stays the same for a given --slots):
- --loop-weighted: the 12 looped layers (6-17) run 3 times per token, so they get more slots than the others, for
  the same total size as --slots N in every layer.
- --policy lfu: evict the expert used least often (counts decay), not the least recently used.

The model code is the open mlx-lm Inkling 2 Mini port (ml-explore/mlx-lm#2086, jundot:inkling2-mini). Run it with the
venv of that checkout:
    ~/Code/mlx-lm-inkling2/.venv/bin/python benches/expert-stream/stream.py --slots 16 --tokens 256
--slots 128 --resident loads every expert up front: the in-memory baseline with the same code. With --ref FILE it
also saves its tokens and logits; any other run given --ref FILE feeds those same tokens and reports how far its
logits are from them (0 = streaming changes nothing). Writes one JSON line of results to --out (and prints it).
"""
import argparse
import fcntl
import json
import os
import re
import struct
import sys
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import mlx.core as mx
import numpy as np
from mlx import nn

from mlx_lm.layers.moe import _gather_sort, _scatter_unsort
from mlx_lm.models.inkling2 import Model, ModelArgs

OLLAMA = Path.home() / "Models/ollama"
MANIFEST = OLLAMA / "manifests-v2/ollama.com/library/inkling-2-mini/26b-a5b-nvfp4"
F_NOCACHE = getattr(fcntl, "F_NOCACHE", 48)
NVFP4_SCALE = 6.0 * 448.0  # e2m1 max x e4m3 max, as in the port's checkpoint.sanitize
NUM_EXPERTS = 128
REF_STEPS = 64
GROUP = 0  # --group: force expert groups of this size on long prompt chunks (exactness check)  # decode steps whose logits --ref compares
PROMPT = ("Write a Python function `parse_duration(s)` that parses ISO 8601 durations such as 'P3DT4H5M' and "
          "returns the total number of seconds. Handle weeks, fractional seconds and invalid input. "
          "Then write five pytest tests for it.")


def blob(digest):
    return OLLAMA / "blobs" / digest.replace(":", "-")


def header(path):
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        return 8 + n, json.loads(f.read(n))


class ExpertFile:
    """One expert tensor blob (w13 or w2 of a layer): the weight and scale rows of expert e, read with pread."""

    def __init__(self, path, name):
        base, hdr = header(path)
        meta = hdr.get("__metadata__", {})
        self.mode, self.group_size = meta["quant_type"], int(meta["group_size"])
        self.bits = 4 if self.mode == "nvfp4" else 8
        w, s = hdr[name], hdr[name + "_scale"]
        self.w_shape, self.s_shape = tuple(w["shape"][1:]), tuple(s["shape"][1:])
        self.w_off, self.s_off = base + w["data_offsets"][0], base + s["data_offsets"][0]
        self.w_size = int(np.prod(self.w_shape)) * 4
        self.s_size = int(np.prod(self.s_shape))
        self.fd = os.open(path, os.O_RDONLY)
        fcntl.fcntl(self.fd, F_NOCACHE, 1)

    def read_into(self, w_buf, s_buf, e):
        """Read expert e straight into rows of preallocated arrays (no extra copy)."""
        for buf, off in ((w_buf, self.w_off + e * self.w_size), (s_buf, self.s_off + e * self.s_size)):
            got = os.preadv(self.fd, [memoryview(buf).cast("B")], off)
            assert got == buf.nbytes, (got, buf.nbytes)

    @property
    def expert_bytes(self):
        return self.w_size + self.s_size


class Slots:
    """N cached experts of one projection: weight, scales and (nvfp4) global scale, in one array each."""

    def __init__(self, src: ExpertFile, n, global_scale):
        self.src, self.n = src, n
        self.weight = mx.zeros((n, *src.w_shape), mx.uint32)
        self.scales = mx.zeros((n, *src.s_shape), mx.uint8)
        self.all_gs = global_scale  # (128,) float32 or None
        self.gs = None if global_scale is None else mx.zeros((n,), mx.float32)

    @property
    def nbytes(self):
        return self.weight.nbytes + self.scales.nbytes

    def buffers(self, m):
        return np.empty((m, *self.src.w_shape), np.uint32), np.empty((m, *self.src.s_shape), np.uint8)

    def put(self, slots, experts, w, s):
        at = mx.array(slots)
        self.weight[at] = mx.array(w)
        self.scales[at] = mx.array(s)
        if self.gs is not None:
            self.gs[at] = self.all_gs[mx.array(experts)]

    def matmul(self, x, idx, sorted_indices):
        src = self.src
        return mx.gather_qmm(x, self.weight, self.scales, None, rhs_indices=idx, transpose=True,
                             group_size=src.group_size, bits=src.bits, mode=src.mode, global_scale=self.gs,
                             sorted_indices=sorted_indices)


class Stats:
    KEYS = ("reads", "bytes", "hits", "lookups", "read_s", "wait_s", "put_s", "prefetched", "prefetch_used")

    def __init__(self):
        for k in self.KEYS:
            setattr(self, k, 0)

    def snap(self):
        return {k: getattr(self, k) for k in self.KEYS}


class StreamedExperts(nn.Module):
    """Stands in for the port's Experts: same math, experts come from Slots, read from disk on a miss."""

    def __init__(self, experts, gate_up: Slots, down: Slots, pool, stats, resident, policy):
        super().__init__()
        self.act_fn = experts.act_fn
        self._gu, self._dn, self._pool, self._stats = gate_up, down, pool, stats
        self._where = OrderedDict()  # expert -> slot, least recently used first
        self._uses = np.zeros(NUM_EXPERTS)  # decayed use counts, for lfu
        self._resident, self._policy = resident, policy
        # Plain attributes, not module children (the visit order points back at these modules):
        object.__setattr__(self, "_pending", {})  # expert -> (future, buffers): read ahead for this layer's next call
        object.__setattr__(self, "_ctx", None)  # shared visit order, for read-ahead (set by load)

    @property
    def n(self):
        return self._gu.n

    def _victim(self, keep):
        cands = [e for e in self._where if e not in keep]
        if self._policy == "lfu":
            return min(cands, key=lambda e: self._uses[e])  # ties: least recently used (dict order)
        return cands[0]

    def fill(self, experts):
        """Put these experts in slots, evicting ones not in `experts`."""
        st = self._stats
        self._uses *= 0.995
        self._uses[experts] += 1
        need = [e for e in experts if e not in self._where]
        st.lookups += len(experts)
        st.hits += len(experts) - len(need)
        for e in experts:
            if e in self._where:
                self._where.move_to_end(e)
        if not need:
            return
        free = sorted(set(range(self.n)) - set(self._where.values()))
        keep = set(experts)
        slots = []
        for e in need:
            if free:
                slots.append(free.pop(0))
            else:
                slots.append(self._where.pop(self._victim(keep)))
        t = time.perf_counter()
        gu_w, gu_s = self._gu.buffers(len(need))
        dn_w, dn_s = self._dn.buffers(len(need))
        jobs, ahead = [], []
        for j, e in enumerate(need):
            if e in self._pending:  # read ahead while the GPU worked: wait for it, then copy it in
                ahead.append((j, self._pending.pop(e)))
            else:
                jobs += [(self._gu.src, gu_w[j], gu_s[j], e), (self._dn.src, dn_w[j], dn_s[j], e)]
        futures = [self._pool.submit(lambda a: a[0].read_into(a[1], a[2], a[3]), job) for job in jobs]
        for j, (fut, bufs) in ahead:
            fut.result()
            gu_w[j], gu_s[j], dn_w[j], dn_s[j] = bufs
        for f in futures:
            f.result()
        st.prefetch_used += len(ahead)
        t2 = time.perf_counter()
        self._gu.put(slots, need, gu_w, gu_s)
        self._dn.put(slots, need, dn_w, dn_s)
        st.put_s += time.perf_counter() - t2
        st.read_s += t2 - t
        st.reads += len(need) - len(ahead)
        st.bytes += (len(need) - len(ahead)) * (self._gu.src.expert_bytes + self._dn.src.expert_bytes)
        for e, s in zip(need, slots):
            self._where[e] = s

    def prefetch(self, experts):
        """Start reading these experts (a guess for this layer's next call) unless cached or already on their way."""
        object.__setattr__(self, "_pending", {e: v for e, v in self._pending.items() if e in experts})  # drop old guesses
        for e in experts:
            if e in self._where or e in self._pending:
                continue
            gu_w, gu_s = self._gu.buffers(1)
            dn_w, dn_s = self._dn.buffers(1)
            bufs = (gu_w[0], gu_s[0], dn_w[0], dn_s[0])

            def read(e=e, b=bufs):
                self._gu.src.read_into(b[0], b[1], e)
                self._dn.src.read_into(b[2], b[3], e)
            self._pending[e] = (self._pool.submit(read), bufs)
            self._stats.prefetched += 1
            self._stats.bytes += self._gu.src.expert_bytes + self._dn.src.expert_bytes

    def _run(self, x, idx, indices_shape, allow_sort=True):
        do_sort = allow_sort and idx.size >= 64
        inv = None
        if do_sort:
            x, idx, inv = _gather_sort(x, idx)
        gate_up = self._gu.matmul(x, idx, do_sort)
        x = self.act_fn(gate_up[..., 0::2], gate_up[..., 1::2])
        x = self._dn.matmul(x, idx, do_sort)
        if do_sort:
            x = _scatter_unsort(x, inv, indices_shape)
        return x.squeeze(-2)

    def __call__(self, x, indices):
        x = mx.expand_dims(x, (-2, -3))
        indices = mx.stop_gradient(indices)
        if self._resident:
            return self._run(x, indices, indices.shape)
        ctx = self._ctx
        guess = None
        if ctx is not None:
            # Read ahead: the next visit's router applied to this hidden state guesses its experts (the hidden state
            # changes little from one layer to the next), so their reads overlap this layer's GPU work.
            visit = ctx["visit"]
            ctx["visit"] = (visit + 1) % len(ctx["order"])
            nxt = ctx["order"][ctx["visit"]]
            if ids_rows(indices) <= 8:
                router = nxt._router
                flat = x.reshape(-1, x.shape[-1]).astype(mx.float32)
                scores = mx.sigmoid(flat @ router.weight.T) + router.e_score_correction_bias
                guess = mx.argpartition(-scores, kth=router.top_k - 1, axis=-1)[..., :router.top_k]
        t = time.perf_counter()
        if guess is not None:
            mx.eval(indices, guess)
        ids = np.array(indices)  # waits for the router: the reads need to know which experts
        self._stats.wait_s += time.perf_counter() - t
        if guess is not None:
            g = [int(e) for e in np.unique(np.array(guess))]
            if len(g) <= nxt.n:
                nxt.prefetch(g)
        unique = [int(e) for e in np.unique(ids)]
        out = None
        # More experts than slots (a prompt chunk): go through them in groups that fit, through the same cache and
        # kernels as decoding; each (token, choice) is computed once, with its own expert, and the rest masked.
        size = min(self.n, GROUP or self.n)
        for g in range(0, len(unique), size):
            group = unique[g:g + size]
            self.fill(group)
            lut = np.zeros(NUM_EXPERTS, np.uint32)
            for e in group:
                lut[e] = self._where[e]
            # Unsorted: the GPU's sorted expert kernel rounds a row differently depending on which rows share its
            # tiles, so slot numbers other than the in-memory ones (and a group's masked rows piling onto one slot)
            # change the result (measured: up to 0.006 per layer, 0.7-1.1 on the logits after 48 visits). Unsorted
            # gives exactly the in-memory run's numbers; only long prompt chunks get slower.
            y = self._run(x, mx.array(lut[ids]), indices.shape, allow_sort=False)
            if len(group) == len(unique):
                return y
            mask = mx.array(np.isin(ids, group))[..., None]
            y = mx.where(mask, y, mx.zeros_like(y))
            out = y if out is None else out + y
            mx.eval(out)  # before the next group overwrites slots this one read
        return out


def ids_rows(indices):
    return int(np.prod(indices.shape[:-1]))


class _Placeholder(nn.Module):
    def __init__(self, act_fn):
        super().__init__()
        self.act_fn = act_fn


def slot_plan(sources, slots, loop_weighted, loop_layers):
    """Slots per layer. Uniform, or (loop_weighted) proportional to visits per token, same total bytes."""
    if not loop_weighted or slots >= NUM_EXPERTS:
        return [slots] * len(sources)
    size = [gu.expert_bytes + dn.expert_bytes for gu, dn in sources]
    budget = slots * sum(size)
    visits = [3 if i in loop_layers else 1 for i in range(len(sources))]
    lo, hi = 0.0, float(NUM_EXPERTS)
    for _ in range(60):  # find k so that slots_i = clamp(k * visits_i) fills the budget
        k = (lo + hi) / 2
        plan = [min(NUM_EXPERTS, max(8, int(k * v))) for v in visits]
        lo, hi = (k, hi) if sum(p * b for p, b in zip(plan, size)) <= budget else (lo, k)
    return [min(NUM_EXPERTS, max(8, int(lo * v))) for v in visits]


def load(a):
    manifest = json.loads(MANIFEST.read_text())
    layers = {l["name"]: l for l in manifest["layers"] if l.get("name")}
    config = json.loads(blob(layers["config.json"]["digest"]).read_text())
    config.pop("vision_config", None)
    model = Model(ModelArgs.from_dict(config))

    expert = re.compile(r"^model\.llm\.layers\.(\d+)\.mlp\.experts\.")
    weights = {}
    for name, l in layers.items():
        if not name.startswith("model.llm.") or expert.match(name):
            continue
        weights.update(mx.load(str(blob(l["digest"])), format="safetensors"))
    weights = model.sanitize(weights)
    # The port builds every layer's 128 experts as random float32 placeholders (~130 GB if evaluated): drop them
    # before anything is evaluated, keeping only the activation.
    decoder = model.model.language_model.layers
    for layer in decoder:
        layer.mlp.experts = _Placeholder(layer.mlp.experts.act_fn)
    model.load_weights(list(weights.items()), strict=False)
    mx.eval(model.parameters())
    dense_gb = mx.get_active_memory() / 1e9

    def tensor(name):
        return mx.load(str(blob(layers[name]["digest"])), format="safetensors")[name]

    sources = []
    for i in range(len(decoder)):
        sources.append(tuple(ExpertFile(str(blob(layers[f"model.llm.layers.{i}.mlp.experts.{w}_weight"]["digest"])),
                                        f"model.llm.layers.{i}.mlp.experts.{w}_weight") for w in ("w13", "w2")))
    plan = slot_plan(sources, a.slots, a.loop_weighted, set(model.args.text.loop_core))
    pool = ThreadPoolExecutor(a.workers)
    stats = Stats()
    for i, layer in enumerate(decoder):
        parts = []
        for w, src in zip(("w13", "w2"), sources[i]):
            name = f"model.llm.layers.{i}.mlp.experts.{w}_weight"
            gs = None
            if src.mode == "nvfp4":
                amax = tensor(name + "_amax").astype(mx.float32)
                den = tensor(name + "_outer_scale_denominator").astype(mx.float32)
                gs = amax * (NVFP4_SCALE / den)
            parts.append(Slots(src, plan[i], gs))
        layer.mlp.experts = StreamedExperts(layer.mlp.experts, *parts, pool, stats, a.resident, a.policy)
        object.__setattr__(layer.mlp.experts, "_router", layer.mlp.gate)
        if a.resident:
            layer.mlp.experts.fill(list(range(NUM_EXPERTS)))
            mx.eval(parts[0].weight, parts[0].scales, parts[1].weight, parts[1].scales)
    if getattr(a, "prefetch", False) and not a.resident:
        ctx = dict(visit=0, order=[l.mlp.experts for l in model.model.language_model.layer_visits])
        for layer in decoder:
            object.__setattr__(layer.mlp.experts, "_ctx", ctx)
    return model, layers, stats, dense_gb, plan


def tokenizer(layers, out_dir):
    from transformers import AutoTokenizer
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"):
        if name in layers and not (out_dir / name).exists():
            (out_dir / name).write_bytes(blob(layers[name]["digest"]).read_bytes())
    return AutoTokenizer.from_pretrained(str(out_dir))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slots", type=int, default=16, help="cached experts per layer (128 = all)")
    ap.add_argument("--resident", action="store_true", help="load every expert up front (needs --slots 128)")
    ap.add_argument("--loop-weighted", action="store_true", help="more slots for the looped layers, same total")
    ap.add_argument("--policy", choices=("lru", "lfu"), default="lru")
    ap.add_argument("--tokens", type=int, default=256)
    ap.add_argument("--workers", type=int, default=16, help="parallel SSD reads")
    ap.add_argument("--ref", default="", help="npz of the resident run's tokens and logits (it writes it)")
    ap.add_argument("--prefetch", action="store_true", help="read the next layer's guessed experts ahead")
    ap.add_argument("--group", type=int, default=0, help="force expert groups of this size (check)")
    ap.add_argument("--prefill-chunk", type=int, default=0, help="prompt tokens per forward (0: all at once)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    assert not a.resident or a.slots == NUM_EXPERTS
    global GROUP
    GROUP = a.group

    t0 = time.perf_counter()
    model, layers, stats, dense_gb, plan = load(a)
    load_s = time.perf_counter() - t0
    tok = tokenizer(layers, Path(__file__).parent / ".tokenizer")
    ids = tok.encode(PROMPT)  # no chat template in Ollama's tokenizer files: plain continuation, speed only

    ref = None
    if a.ref and not a.resident:
        ref = np.load(a.ref)
    forced = None if ref is None else [int(t) for t in ref["tokens"]]

    cache = model.make_cache()
    mx.reset_peak_memory()
    t = time.perf_counter()
    step = a.prefill_chunk or len(ids)  # 1: the prompt one token at a time (no expert groups), for the exactness check
    for i in range(0, len(ids), step):
        logits = model(mx.array(ids[i:i + step])[None], cache=cache)[:, -1]
        mx.eval(logits)
    y = mx.argmax(logits, axis=-1)
    mx.eval(y)
    prefill_s = time.perf_counter() - t
    after_prefill = stats.snap()

    kept = [np.array(logits[0].astype(mx.float32))]  # logits of the first REF_STEPS + 1 positions
    out, times = [int(y.item())], []
    for step in range(a.tokens - 1):
        if forced is not None and step < len(forced) - 1:
            y = mx.array([forced[step]])  # feed the reference token so both runs see the same text
        t = time.perf_counter()
        logits = model(y[None], cache=cache)[:, -1]
        y = mx.argmax(logits, axis=-1)
        mx.eval(y)
        times.append(time.perf_counter() - t)
        out.append(int(y.item()))
        if step < REF_STEPS:
            kept.append(np.array(logits[0].astype(mx.float32)))
    s = stats.snap()
    dec = {k: s[k] - after_prefill[k] for k in s}
    half = times[len(times) // 2:]
    n = len(times)
    per = lambda k: round(dec[k] / n * 1000, 1)  # noqa: E731  ms per token
    res = dict(
        slots=a.slots, loop_weighted=a.loop_weighted, policy=a.policy, resident=a.resident, slot_plan=plan,
        tokens=a.tokens, prompt_tokens=len(ids),
        expert_cache_gb=round(sum(l.mlp.experts._gu.nbytes + l.mlp.experts._dn.nbytes
                                  for l in model.model.language_model.layers) / 1e9, 2),
        dense_gb=round(dense_gb, 2), peak_gb=round(mx.get_peak_memory() / 1e9, 2), load_s=round(load_s, 1),
        prefill_tok_s=round(len(ids) / prefill_s, 1),
        decode_tok_s=round(n / sum(times), 2), decode_tok_s_second_half=round(len(half) / sum(half), 2),
        ms_per_token=round(sum(half) / len(half) * 1000, 1),
        ms_read=per("read_s"), ms_router_wait=per("wait_s"), ms_cache_write=per("put_s"),
        hit_rate=round(dec["hits"] / max(dec["lookups"], 1), 3), prefetch=getattr(a, "prefetch", False),
        prefetch_used_share=round(dec["prefetch_used"] / dec["prefetched"], 3) if dec["prefetched"] else None,
        mb_read_per_token=round(dec["bytes"] / 1e6 / n, 1),
        disk_gb_s=round(dec["bytes"] / 1e9 / dec["read_s"], 2) if dec["read_s"] else None,
        text=tok.decode(out)[:300],
    )
    if a.resident and a.ref:
        np.savez(a.ref, tokens=np.array(out), logits=np.stack(kept))
    if ref is not None:
        r = ref["logits"][:len(kept)]
        diff = np.abs(np.stack(kept) - r).max(axis=-1)
        same = (np.stack(kept).argmax(-1) == r.argmax(-1))
        res.update(ref_max_logit_diff=round(float(diff.max()), 4), ref_prompt_logit_diff=round(float(diff[0]), 4),
                   ref_first_argmax_mismatch=None if same.all() else int(np.argmin(same)))
    line = json.dumps(res)
    print(line)
    if a.out:
        with open(a.out, "a") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())
