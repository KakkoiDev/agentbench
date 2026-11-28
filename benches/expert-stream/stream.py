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
LFU_DECAY = 0.995  # --lfu-decay: how fast LFU forgets (1.0: never)
GUESS_FIRST = False  # --guess-first: queue the guesses before this call's own misses (v3 order)
AHEAD = 1  # --ahead: visits read ahead
LOSSY = ""  # --lossy: "" (exact), "skip", "skiplow", "sub" or "topk" (see route())
SKIP_SHARE = 0.1  # --skip-share: skiplow drops a missing expert only below this share of its row's router weight
SUB_ALPHA = 0.8  # --sub-alpha: a cached stand-in needs this share of the missing expert's router score
TOPK = 8  # --topk: experts per token in "topk" mode
SCRATCH = 0  # --prefill-scratch: full-layer buffers for prompt chunks (0: off, through the slots; 1: on; 2: also read
#              the next visit's experts ahead into a second buffer)
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
    KEYS = ("reads", "bytes", "hits", "lookups", "read_s", "wait_s", "put_s", "prefetched", "prefetch_used", "picks",
            "dropped", "substituted", "absorbed")

    def __init__(self):
        for k in self.KEYS:
            setattr(self, k, 0)

    def snap(self):
        return {k: getattr(self, k) for k in self.KEYS}


_scratch = {}  # layer kind (tensor shapes) -> [(mx arrays, writable numpy views of them)]


def scratch_buffers(gu, dn, avoid=None):
    """A full 128-expert buffer set for a layer kind, other than `avoid` (None when only `avoid` exists).

    Prompt chunks (2048 tokens touch nearly every expert) used to go through the slots in groups: each group re-ran
    the matmul on every row (masked), so with 16 slots a chunk cost 8x the compute, unsorted, plus a GPU wait per
    group. Instead the chunk's experts are read straight into a full-size buffer at their own rows, and the layer runs
    once with the router's own ids, the same call as the in-memory model (so it is bit-identical to it). The arrays
    are MLX arrays read into through numpy views of their memory (no copy). Safe because a buffer is only written
    after the router wait of a later layer, which means the GPU has finished the layer that read it."""
    key = (gu.src.w_shape, gu.src.s_shape, dn.src.w_shape, dn.src.s_shape)
    have = _scratch.setdefault(key, [])
    for b in have:
        if b is not avoid:
            return b
    if len(have) >= SCRATCH:
        return None
    arrays = tuple(mx.zeros((NUM_EXPERTS, *shape), dt) for shape, dt in (
        (gu.src.w_shape, mx.uint32), (gu.src.s_shape, mx.uint8), (dn.src.w_shape, mx.uint32), (dn.src.s_shape, mx.uint8)))
    mx.eval(*arrays)
    views = tuple(np.asarray(memoryview(t)) for t in arrays)
    for t, v in zip(arrays, views):
        assert v.flags.writeable and v.nbytes == t.nbytes and v.flags.c_contiguous
    have.append((arrays, views))
    return have[-1]


def runs(experts, cap=8):
    """Sorted experts as runs of consecutive ids (one read each), at most `cap` per run."""
    out = []
    for e in sorted(experts):
        if out and e == out[-1][-1] + 1 and len(out[-1]) < cap:
            out[-1].append(e)
        else:
            out.append([e])
    return out


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
        self._uses *= LFU_DECAY
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
        if not LOSSY:
            self._pending.clear()  # guesses for this call that were not needed
        t2 = time.perf_counter()
        self._gu.put(slots, need, gu_w, gu_s)
        self._dn.put(slots, need, dn_w, dn_s)
        st.put_s += time.perf_counter() - t2
        st.read_s += t2 - t
        st.reads += len(need) - len(ahead)
        st.bytes += (len(need) - len(ahead)) * (self._gu.src.expert_bytes + self._dn.src.expert_bytes)
        for e, s in zip(need, slots):
            self._where[e] = s

    def absorb(self):
        """Lossy modes: experts whose background read finished go into the cache (no waiting)."""
        done = [e for e, (fut, _) in self._pending.items() if fut.done() and e not in self._where]
        for e in done[:self.n // 2]:  # never refill more than half the cache at once
            _, bufs = self._pending.pop(e)
            free = sorted(set(range(self.n)) - set(self._where.values()))
            slot = free[0] if free else self._where.pop(self._victim(set()))
            self._gu.put([slot], [e], bufs[0][None], bufs[1][None])
            self._dn.put([slot], [e], bufs[2][None], bufs[3][None])
            self._where[e] = slot
            self._stats.absorbed += 1

    def route(self, hidden, selected, weights, gate, scores_all=None):
        """Lossy routing for decode rows (8 rows or fewer; a prompt stays exact). Returns new (selected, weights).
        - skip: a picked expert that is not cached is left out and the others' weights scaled up to the same sum;
          it is read in the background and joins the cache for later tokens.
        - sub: a picked expert that is not cached is replaced by the best-scoring cached expert not already picked,
          when its router score is at least SUB_ALPHA of the missing one's (else it is read as usual); weights are
          the router's, renormalized over the new set. Missing ones are read in the background.
        - topk: only the TOPK highest-weighted experts of the 8, renormalized to the same sum."""
        if not LOSSY or ids_rows(selected) > 8:
            return selected, weights
        self.absorb()
        ids, w = np.array(selected).astype(np.int64), np.array(weights.astype(mx.float32))
        flat_ids, flat_w = ids.reshape(-1, ids.shape[-1]), w.reshape(-1, w.shape[-1])
        st = self._stats
        if LOSSY == "topk":
            order = np.argsort(-flat_w, axis=-1)[:, :TOPK]
            new_ids = np.take_along_axis(flat_ids, order, -1)
            new_w = np.take_along_axis(flat_w, order, -1)
            new_w *= flat_w.sum(-1, keepdims=True) / new_w.sum(-1, keepdims=True)
            st.picks += flat_ids.size
            st.dropped += flat_ids.size - new_ids.size
            shape = (*ids.shape[:-1], TOPK)
            return mx.array(new_ids.reshape(shape).astype(np.uint32)), mx.array(new_w.reshape(shape))
        cached = set(self._where)
        if LOSSY == "sub":
            scores = np.array(scores_all).reshape(-1, scores_all.shape[-1])  # computed with the router, one GPU wait
            biased = scores + np.array(gate.e_score_correction_bias)
        missing_all = set()
        for r in range(flat_ids.shape[0]):
            row = flat_ids[r]
            st.picks += len(row)
            miss = [j for j, e in enumerate(row) if e not in cached]
            if not miss or len(miss) == len(row):
                continue  # all cached, or nothing to fall back on: exact for this row
            if LOSSY == "skiplow":
                # Drop only the missing experts that carry little of this token's weight; read the others as usual.
                # Dropped ones are not fetched: they matter little, and their reads would compete with the ones
                # the model waits on.
                total = flat_w[r].sum()
                low = [j for j in miss if flat_w[r, j] < SKIP_SHARE * total]
                keep_any = [e for j, e in enumerate(row) if j not in low]
                if low and keep_any:
                    stand_in = keep_any[0]
                    for j in low:
                        flat_w[r, j] = 0.0
                        row[j] = stand_in
                    flat_w[r] *= total / flat_w[r].sum()
                    st.dropped += len(low)
                continue
            missing_all.update(int(row[j]) for j in miss)
            if LOSSY == "skip":
                total = flat_w[r].sum()
                stand_in = next(e for e in row if e in cached)
                for j in miss:
                    flat_w[r, j] = 0.0
                    row[j] = stand_in  # weight 0: no effect, no read
                flat_w[r] *= total / flat_w[r].sum()
                st.dropped += len(miss)
            else:
                taken = set(int(e) for e in row)
                cands = sorted((e for e in cached if e not in taken), key=lambda e: -biased[r, e])
                changed = False
                for j in miss:
                    e = int(row[j])
                    if cands and biased[r, cands[0]] >= SUB_ALPHA * biased[r, e]:
                        row[j] = cands.pop(0)
                        st.substituted += 1
                        changed = True
                if changed:
                    sc = scores[r, row]
                    flat_w[r] = sc / (sc.sum() + 1e-20) * gate.routed_scaling_factor
        if missing_all:
            self.prefetch(sorted(missing_all))  # background: they join the cache later (absorb)
        return mx.array(flat_ids.reshape(ids.shape).astype(np.uint32)), mx.array(flat_w.reshape(w.shape))

    def prefetch(self, experts):
        """Start reading these experts (a guess for this layer's next call) unless cached or already on their way."""
        for e in experts:
            if e in self._where or e in self._pending:
                continue
            if len(self._pending) >= self.n:  # bounded: at most one cache's worth of reads in flight per layer
                break
            gu_w, gu_s = self._gu.buffers(1)
            dn_w, dn_s = self._dn.buffers(1)
            bufs = (gu_w[0], gu_s[0], dn_w[0], dn_s[0])

            def read(e=e, b=bufs):
                self._gu.src.read_into(b[0], b[1], e)
                self._dn.src.read_into(b[2], b[3], e)
            self._pending[e] = (self._pool.submit(read), bufs)
            self._stats.prefetched += 1
            self._stats.bytes += self._gu.src.expert_bytes + self._dn.src.expert_bytes

    def read_full(self, experts, buf):
        """Start reading these experts into a full-size buffer, at their own rows. Returns the futures."""
        views = buf[1]
        futs = []
        for run in runs(experts):
            lo, hi = run[0], run[-1] + 1
            for src, w, s in ((self._gu.src, views[0], views[1]), (self._dn.src, views[2], views[3])):
                futs.append(self._pool.submit(src.read_into, w[lo:hi], s[lo:hi], lo))
        return futs

    def prefill(self, x, indices, unique, guess):
        """A prompt chunk: the layer's experts read into a full-size buffer, one matmul with the router's own ids."""
        ctx = self._ctx if self._ctx is not None else {}
        st = self._stats
        have, futs, buf = set(), [], None
        pend = ctx.pop("scratch", None)
        if pend is not None:
            target, p_set, p_futs, p_buf = pend
            if target is self:
                have, futs, buf = p_set, p_futs, p_buf
            else:
                for f in p_futs:  # a wrong guess: finish its reads before that buffer can be handed out again
                    f.result()
        if buf is None:
            buf = scratch_buffers(self._gu, self._dn)
        t = time.perf_counter()
        need = [e for e in unique if e not in have]
        futs = futs + self.read_full(need, buf)
        # The next visit's guessed experts into another buffer, queued after this layer's own reads.
        for nxt, gl in guess or []:
            nb = scratch_buffers(nxt._gu, nxt._dn, avoid=buf)
            if nb is not None:
                ctx["scratch"] = (nxt, set(gl), nxt.read_full(gl, nb), nb)
                st.prefetched += len(gl)
                st.bytes += len(gl) * (nxt._gu.src.expert_bytes + nxt._dn.src.expert_bytes)
            break
        for f in futs:
            f.result()
        st.read_s += time.perf_counter() - t
        st.lookups += len(unique)
        st.prefetch_used += len(set(unique) & have)
        st.reads += len(need)
        st.bytes += len(need) * (self._gu.src.expert_bytes + self._dn.src.expert_bytes)
        gu_w, gu_s, dn_w, dn_s = buf[0]
        do_sort = indices.size >= 64  # as the in-memory model does
        idx, inv = indices, None
        if do_sort:
            x, idx, inv = _gather_sort(x, indices)
        kw = dict(transpose=True, sorted_indices=do_sort)
        g, d = self._gu.src, self._dn.src
        y = mx.gather_qmm(x, gu_w, gu_s, None, rhs_indices=idx, group_size=g.group_size, bits=g.bits, mode=g.mode,
                          global_scale=self._gu.all_gs, **kw)
        y = self.act_fn(y[..., 0::2], y[..., 1::2])
        y = mx.gather_qmm(y, dn_w, dn_s, None, rhs_indices=idx, group_size=d.group_size, bits=d.bits, mode=d.mode,
                          global_scale=self._dn.all_gs, **kw)
        if do_sort:
            y = _scatter_unsort(y, inv, indices.shape)
        return y.squeeze(-2)

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
        guess = pguess = None
        if ctx is not None:
            # Read ahead: the next visit's router applied to this hidden state guesses its experts (the hidden state
            # changes little from one layer to the next), so their reads overlap this layer's GPU work.
            visit = ctx["visit"]
            n_visits = len(ctx["order"])
            ctx["visit"] = (visit + 1) % n_visits
            if ids_rows(indices) <= 8:
                flat = x.reshape(-1, x.shape[-1]).astype(mx.float32)
                guess = []
                for d in range(1, AHEAD + 1):
                    nxt = ctx["order"][(visit + d) % n_visits]
                    router = nxt._router
                    scores = mx.sigmoid(flat @ router.weight.T) + router.e_score_correction_bias
                    guess.append((nxt, mx.argpartition(-scores, kth=router.top_k - 1, axis=-1)[..., :router.top_k]))
            elif SCRATCH >= 2:  # a prompt chunk: the next visit's router on this hidden state, as in decoding
                nxt = ctx["order"][(visit + 1) % n_visits]
                router = nxt._router
                flat = x.reshape(-1, x.shape[-1]).astype(mx.float32)
                scores = mx.sigmoid(flat @ router.weight.T) + router.e_score_correction_bias
                pguess = [(nxt, mx.argpartition(-scores, kth=router.top_k - 1, axis=-1)[..., :router.top_k])]
        t = time.perf_counter()
        if guess or pguess:
            mx.eval(indices, [g for _, g in guess or pguess])
        ids = np.array(indices)  # waits for the router: the reads need to know which experts
        self._stats.wait_s += time.perf_counter() - t
        if guess:
            # Read this call's own misses first (fill below waits for them), then the guesses: queued the other way,
            # the guesses delayed the reads this layer waits on when the SSD is the limit.
            object.__setattr__(self, "_guess", [(nxt, [int(e) for e in np.unique(np.array(g))]) for nxt, g in guess])
            if GUESS_FIRST:  # the v3 order: guesses queued before this call's own misses (more reads in flight)
                for nxt, gl in self._guess:
                    if len(gl) <= nxt.n:
                        nxt.prefetch(gl)
                object.__setattr__(self, "_guess", None)
        unique = [int(e) for e in np.unique(ids)]
        if SCRATCH and len(unique) > self.n:
            pg = [(nxt, [int(e) for e in np.unique(np.array(g))]) for nxt, g in pguess or []]
            return self.prefill(x, indices, unique, pg)
        out = None
        # More experts than slots (a prompt chunk): go through them in groups that fit, through the same cache and
        # kernels as decoding; each (token, choice) is computed once, with its own expert, and the rest masked.
        size = min(self.n, GROUP or self.n)
        for g in range(0, len(unique), size):
            group = unique[g:g + size]
            self.fill(group)
            if not LOSSY:
                self._pending.clear()
            for nxt, gl in getattr(self, "_guess", None) or []:
                if len(gl) <= nxt.n:
                    nxt.prefetch(gl)
            object.__setattr__(self, "_guess", None)
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


from mlx_lm.models.inkling2.layers import SparseMoeBlock  # noqa: E402

_moe_call = SparseMoeBlock.__call__


def _streamed_moe_call(self, hidden_states):
    """The port's MoE block, with a lossy mode's routing change between the router and the experts."""
    if not LOSSY or not isinstance(self.experts, StreamedExperts) or self.experts._resident:
        return _moe_call(self, hidden_states)
    selected, weights = self.gate(hidden_states)
    scores_all = None
    if LOSSY == "sub" and ids_rows(selected) <= 8:
        scores_all = mx.sigmoid(hidden_states.astype(mx.float32) @ self.gate.weight.T)
        mx.eval(selected, weights, scores_all)
    selected, weights = self.experts.route(hidden_states, selected, weights, self.gate, scores_all)
    states = self.experts(hidden_states, selected).astype(mx.float32) * weights[..., None]
    return states.sum(axis=-2).astype(hidden_states.dtype)


SparseMoeBlock.__call__ = _streamed_moe_call


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
    ap.add_argument("--guess-first", action="store_true")
    ap.add_argument("--ahead", type=int, default=1, help="visits to read ahead (with --prefetch)")
    ap.add_argument("--lfu-decay", type=float, default=0.995)
    ap.add_argument("--lossy", choices=("", "skip", "skiplow", "sub", "topk"), default="")
    ap.add_argument("--skip-share", type=float, default=0.1)
    ap.add_argument("--sub-alpha", type=float, default=0.8)
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--group", type=int, default=0, help="force expert groups of this size (check)")
    ap.add_argument("--prefill-chunk", type=int, default=0, help="prompt tokens per forward (0: all at once)")
    ap.add_argument("--prefill-scratch", type=int, default=0, choices=(0, 1, 2),
                    help="prompt chunks through full-layer buffers (1), and read the next visit ahead (2)")
    ap.add_argument("--prompt-tokens", type=int, default=0, help="repeat the prompt to this many tokens (prefill speed)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    assert not a.resident or a.slots == NUM_EXPERTS
    global GROUP, AHEAD, LFU_DECAY, GUESS_FIRST, LOSSY, SUB_ALPHA, TOPK
    GROUP, AHEAD, LFU_DECAY, GUESS_FIRST = a.group, a.ahead, a.lfu_decay, a.guess_first
    LOSSY, SUB_ALPHA, TOPK = a.lossy, a.sub_alpha, a.topk
    global SKIP_SHARE, SCRATCH
    SKIP_SHARE, SCRATCH = a.skip_share, a.prefill_scratch

    t0 = time.perf_counter()
    model, layers, stats, dense_gb, plan = load(a)
    load_s = time.perf_counter() - t0
    tok = tokenizer(layers, Path(__file__).parent / ".tokenizer")
    ids = tok.encode(PROMPT)  # no chat template in Ollama's tokenizer files: plain continuation, speed only
    if a.prompt_tokens:
        ids = (ids * (a.prompt_tokens // len(ids) + 1))[:a.prompt_tokens]

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
    agree = [] if forced is None else [out[0] == forced[0]]
    for step in range(a.tokens - 1):
        if forced is not None and step < len(forced) - 1:
            y = mx.array([forced[step]])  # feed the reference token so both runs see the same text
        t = time.perf_counter()
        logits = model(y[None], cache=cache)[:, -1]
        y = mx.argmax(logits, axis=-1)
        mx.eval(y)
        times.append(time.perf_counter() - t)
        out.append(int(y.item()))
        if forced is not None and step + 1 < len(forced):
            agree.append(out[-1] == forced[step + 1])  # same next token as the exact model, on the same text
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
        prefill_tok_s=round(len(ids) / prefill_s, 1), prefill_s=round(prefill_s, 2), prefill_scratch=a.prefill_scratch,
        prefill_gb_read=round(after_prefill["bytes"] / 1e9, 2),
        decode_tok_s=round(n / sum(times), 2), decode_tok_s_second_half=round(len(half) / sum(half), 2),
        ms_per_token=round(sum(half) / len(half) * 1000, 1),
        ms_read=per("read_s"), ms_router_wait=per("wait_s"), ms_cache_write=per("put_s"),
        hit_rate=round(dec["hits"] / max(dec["lookups"], 1), 3), prefetch=getattr(a, "prefetch", False), ahead=a.ahead, lfu_decay=a.lfu_decay, guess_first=a.guess_first,
        prefetch_used_share=round(dec["prefetch_used"] / dec["prefetched"], 3) if dec["prefetched"] else None,
        mb_read_per_token=round(dec["bytes"] / 1e6 / n, 1),
        disk_gb_s=round(dec["bytes"] / 1e9 / dec["read_s"], 2) if dec["read_s"] else None,
        text=tok.decode(out)[:300], lossy=a.lossy, sub_alpha=a.sub_alpha if a.lossy == "sub" else None,
        skip_share=a.skip_share if a.lossy == "skiplow" else None,
        topk=a.topk if a.lossy == "topk" else None,
        dropped_share=round(dec["dropped"] / dec["picks"], 3) if dec["picks"] else None,
        substituted_share=round(dec["substituted"] / dec["picks"], 3) if dec["picks"] else None,
    )
    if a.resident and a.ref:
        np.savez(a.ref, tokens=np.array(out), logits=np.stack(kept))
    if ref is not None:
        r = ref["logits"][:len(kept)]
        diff = np.abs(np.stack(kept) - r).max(axis=-1)
        same = (np.stack(kept).argmax(-1) == r.argmax(-1))
        res.update(ref_max_logit_diff=round(float(diff.max()), 4), ref_prompt_logit_diff=round(float(diff[0]), 4),
                   ref_first_argmax_mismatch=None if same.all() else int(np.argmin(same)))
        k = np.stack(kept).astype(np.float64)
        rr = r.astype(np.float64)
        lp = rr - rr.max(-1, keepdims=True); lp -= np.log(np.exp(lp).sum(-1, keepdims=True))
        lq = k - k.max(-1, keepdims=True); lq -= np.log(np.exp(lq).sum(-1, keepdims=True))
        res.update(agreement=round(float(np.mean(agree)), 4) if agree else None,
                   kl_mean=round(float((np.exp(lp) * (lp - lq)).sum(-1).mean()), 5))
    line = json.dumps(res)
    print(line)
    if a.out:
        with open(a.out, "a") as f:
            f.write(line + "\n")


if __name__ == "__main__":
    sys.exit(main())
