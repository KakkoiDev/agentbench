# One MoE layer, CPU: resident (128 slots) vs 8 slots on a 57-row prompt chunk. Finds where the grouped path differs.
import sys, json, types
sys.path.insert(0, 'benches/expert-stream')
import mlx.core as mx, numpy as np

import stream
from concurrent.futures import ThreadPoolExecutor
layers = {l["name"]: l for l in json.loads(stream.MANIFEST.read_text())["layers"] if l.get("name")}
L = int(sys.argv[1]) if len(sys.argv) > 1 else 0
def tensor(n): return mx.load(str(stream.blob(layers[n]["digest"])), format="safetensors")[n]
def make(n):
    parts = []
    for w in ("w13", "w2"):
        name = f"model.llm.layers.{L}.mlp.experts.{w}_weight"
        src = stream.ExpertFile(str(stream.blob(layers[name]["digest"])), name)
        gs = None
        if src.mode == "nvfp4":
            gs = tensor(name + "_amax").astype(mx.float32) * (stream.NVFP4_SCALE / tensor(name + "_outer_scale_denominator").astype(mx.float32))
        parts.append(stream.Slots(src, n, gs))
    act = types.SimpleNamespace(act_fn=lambda g, u: (g * mx.sigmoid(g)) * u)
    return stream.StreamedExperts(act, *parts, ThreadPoolExecutor(8), stream.Stats(), False, "lru")
mx.random.seed(0)
x = (mx.random.normal((1, 57, 2304)) * 0.5).astype(mx.bfloat16)
idx = mx.array(np.random.default_rng(0).integers(0, 128, (1, 57, 8)).astype(np.uint32))
full, small = make(128), make(8)
a = full(x, idx); b = small(x, idx); mx.eval(a, b)
d = np.abs(np.array(a.astype(mx.float32)) - np.array(b.astype(mx.float32)))
print("layer", L, "max diff", d.max(), "rows differing", int((d.max(-1) > 0).sum()), "of", d.shape[:-1])
bad = np.argwhere(d.max(-1) > 0)[:5]; print(bad)
# Same with the sorted kernel off (the prompt path sorts when 64+ rows), and with the global scale left out of the
# kernel and applied to the output instead.
orig = stream.StreamedExperts._run
def unsorted(self, x, idx, shape):
    gate_up = self._gu.matmul(x, idx, False)
    y = self.act_fn(gate_up[..., 0::2], gate_up[..., 1::2])
    return self._dn.matmul(y, idx, False).squeeze(-2)
stream.StreamedExperts._run = unsorted
full2, small2 = make(128), make(8)
a2 = full2(x, idx); b2 = small2(x, idx); mx.eval(a2, b2)
print("unsorted: full vs small", float(mx.abs(a2.astype(mx.float32) - b2.astype(mx.float32)).max()),
      "| sorted full vs unsorted full", float(mx.abs(a.astype(mx.float32) - a2.astype(mx.float32)).max()))
