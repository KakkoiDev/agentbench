# vision-v3

30 generated image tasks, harder than vision-v1/v2 (a hosted DeepSeek V4.1 Flash got 25/25 on v1 and 9/10 on v2).
Target: a strong model lands around 40-60%.

- `images/` 1024-1600 px PNGs, rendered by headless Chromium from `benches/vision/build_v3.py` (seed 3, deterministic content).
- `tasks.json` same format as v1/v2 (`id, category, question, answer, score, tol, image`), run with `vision_bench.py run --bench vision-v3`.
- `data.json` the exact content drawn in each image (rows, values, positions), used by the check.
- `check.py` no model calls: images exist and are 1024-1600 px, categories are in `GROUPS`, every answer is re-derived
  from `data.json` by separate solver code, ambiguity guards hold (no near-ties, clear nearest object, nothing on a
  threshold line or border), and `grade()` accepts the answer and rejects a nearby wrong one.

| group | tasks |
|---|---|
| OCR (8) | 26-line receipt: sum of the lines marked T · 12 rotated/upside-down parcel labels, read one tracking code · faint 7-digit ref in light gray · ramen menu in Japanese, two similar names, add prices · six vertical (tategaki) Japanese signs, transcribe the red one · French café menu with near-duplicate items, add two prices · French notice among other notes, transcribe line 4 · handwritten expense list, sum skipping crossed-out lines |
| charts (5) | grouped bars (32 bars, 9 px value labels, 4 shades of blue) sum one series · 6 crossing lines (solid/dashed share colours) difference at one month · stacked bars, height of one middle segment · scatter with 62 points, count blue triangles above a line · 22 horizontal bars, count above 70 |
| counting (4) | 31 red circles among orange/pink · five-pointed stars among 6/7-pointed · letter B in a 26x18 grid of B/8/R/P/E/3/D/H/6 · rotated hollow squares among filled squares, circles, hexagons |
| layout (4) | nearest of 30 numbered circles to a star · grid offset from a marker (with decoy markers) · blue squares inside a dashed box (decoy circles/teal squares) · box right of the only yellow box on shelf 2 |
| UI (4) | toggles on in one section incl. faded ones · 3rd disabled button among 24 incl. gray "ghost" buttons · sum of ticked rows in a 20-row table · sum of red badges, ignoring gray ones |
| tables (3) | 26-row staff table, sum hours where dept is exactly "Sales" (decoy "Sales Ops") · train timetable, travel minutes between two stations · 24-row inventory, count stock below reorder level |
| multi-step (2) | bar chart argmax x price from a side table · handwritten order note priced from a 16-row price list |

Why it is harder than v2: v2 asked for one salient value on a small, clean image (one bar, one toggle, one sign).
v3 needs reading many small items (8-26 values per answer), separating near-identical distractors (near colours,
star points, B vs 8, Sales vs Sales Ops, disabled vs ghost buttons), reading rotated/vertical/faint text, and
combining a lookup with arithmetic, so one misread anywhere makes the answer wrong. 25 of 30 tasks score as `number`
(last number in the reply, `tol` 0 except money at 0.005-0.01); the 5 `text` tasks are a tracking code, two
transcriptions (Japanese, French), and a button label. Money answers ask for a dot decimal separator because the
grader reads "20,90" as 2090.

Rebuild and validate (no model is called):

    .venv/bin/python benches/vision/build_v3.py
    .venv/bin/python benches/vision-v3/check.py
