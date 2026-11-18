# Local AI on a 32 GB Mac mini: findings

A research note on what local models can do as a household assistant, a coding agent, a
translator, a transcriber and an image reader, all on one Mac mini (Apple M6, 32 GB of memory).
It reports what we measured, how, and how far each result can be trusted.

<!-- generated:stats -->
> **247 bench runs** (15 more set aside, with the reason) across 11 benches and 22 models, 2026-11-14 to 2026-11-18. The coding-agent runs alone took 34 hours.
<!-- /generated:stats -->

**Reading the numbers.** Every number in this document is generated from the bench results by
`scripts/collect_local_ai.py`; none is typed by hand. Scores list every repeat (`4/4/5 of 5` is three
runs). The data behind each table is in [`summary.json`](../site/data/local-ai/summary.json) (one entry
per bench, model and setup), [`runs.json`](../site/data/local-ai/runs.json) /
[`runs.csv`](../site/data/local-ai/runs.csv) (one row per run, including runs set aside and why) and
[`schema.json`](../site/data/local-ai/schema.json).

## Abstract

We ran small, repeatable benches of local models (Gemma 4 E4B, 12B and 26B, gpt-oss 20B, Qwen 3.8 27B,
Whisper) on the jobs a home assistant and a coding agent actually do. Five results stand out.
**Thinking (reasoning before answering) made translation and speech recognition worse and many times
slower**, while helping image reading a little. **One mid-size model beat a pair of smaller ones** on
multi-step file jobs, and a 12B Gemma matched the 27B Qwen there with less memory, though it cannot
code. **Prompt rules and after-turn checks ("the harness") lift weak models and can hurt strong
ones**: a single rule made the 12B clear a folder it should have asked about. **Coding-agent extensions
and self-review passes did not improve models that already call tools well.** And **memory has to be
planned by what a model takes once loaded, not by its download size**.

## Setup

- **Machine:** one Mac mini, Apple M6 with 32 GB of unified memory, in everyday use (not a quiet lab
  machine). Versions are in the table below. One GPU job ran at a time (a lock file enforces it).
- **Models** run through [Ollama](https://ollama.com); tags ending in `-mlx` use Ollama's MLX runner,
  which answers one request at a time. Whisper runs through mlx-whisper.
- **Benches**, each small enough to repeat:

| Bench | What it measures | Size | Graded by |
|---|---|---|---|
| Coding agent (v1, v2, v3) | the [pi](https://github.com/badlogic/pi-mono) agent fixes bugs, adds features, refactors, speeds up code | 5 tasks per suite | hidden tests the agent never sees |
| Assistant (English, Japanese/French) | DropBox's assistant, mini, in a chat: answers the asker, saves files where asked, stays quiet on chatter | 14 and 8 requests | rules per request |
| Family jobs | multi-step jobs in a made-up family's files: sort notes, rename photos by date, total a budget, summarize meetings, ask before deleting | 12 jobs in en/ja/fr | the resulting files and reply |
| Translation | short chat messages between English, Japanese and French | 6 directions | chrF against references |
| Speech to text | spoken clips in en/ja/fr, clean and noisy | 3 languages × 2 conditions | word (ja: character) error rate |
| Images | screenshots, documents, photos, charts | 25 + 10 images | expected answers |
| Routing | should a chat message go to the chat model or the tool model? | 53 labelled messages | labels |

<!-- generated:machine -->
| Chip | Memory | macOS | Ollama | pi (coding agent) |
|---|---|---|---|---|
| Apple M6 | 32 GB | 27.0.1 | 0.40.16 | 1.1.0 |
<!-- /generated:machine -->

**Words used here.** *Thinking* is the model reasoning privately before it answers (Ollama's `think`;
for gpt-oss a level: low, medium, high). *Harness* is the code around the model in DropBox's
assistant: house rules in the prompt, plus checks after each turn that send the model back when it
claimed a change no file shows, or answered without looking at the files. Versions v2, v3 and v3.1 are
described in [F6](#f6-the-harness-lifts-weak-models-and-one-rule-backfired-on-a-strong-one).
*Extensions* are coding-agent add-ons for pi: `local-model-guard` repairs broken tool calls and stops
loops; `coding-boost` adds coding hints and an optional self-review pass.

## Findings

### F1. Thinking makes translation and speech recognition worse, and much slower

With thinking on, every model translated worse than with it off, and took many times longer per
message. The same held for speech recognition by models that hear. The model "reasons" about a
sentence that only needed a direct rendering, and sometimes never returns an answer at all (counted as
errors).

<!-- generated:f_thinking_lang -->
| Task | Model | Thinking | Score (chrF higher is better; errors lower is better) | Time | Answers that failed |
|---|---|---|---|---|---|
| Translation | gemma4:12b-mlx | off | chrF 71.5 | 0.52 s per message | 0 |
| Translation | gemma4:12b-mlx | on | chrF 55.3 | 154.74 s per message | 17 |
| Translation | gemma4:e4b-mlx | off | chrF 73.8 | 0.22 s per message | 0 |
| Translation | gemma4:e4b-mlx | on | chrF 70.8 | 2.52 s per message | 0 |
| Translation | gpt-oss:20b | high | chrF 63.2 | 42.26 s per message | 4 |
| Translation | gpt-oss:20b | low | chrF 68.8 | 1.85 s per message | 0 |
| Translation | gpt-oss:20b | medium | chrF 68.4 | 3.85 s per message | 0 |
| Translation | qwen3.8:27b-mlx | off | chrF 68.3 | 1.13 s per message | 0 |
| Translation | qwen3.8:27b-mlx | on | chrF 66.7 | 22.45 s per message | 0 |
| Speech to text | gemma4:12b-mlx | off | 34.1% errors | 2.845 s per s of audio | 13 |
| Speech to text | gemma4:12b-mlx | on | 35.9% errors | 9.237 s per s of audio | 32 |
| Speech to text | gemma4:e4b-mlx | off | 17.5% errors | 0.084 s per s of audio | 0 |
| Speech to text | gemma4:e4b-mlx | on | 20.7% errors | 0.344 s per s of audio | 0 |
<!-- /generated:f_thinking_lang -->

**What to do:** turn thinking off for translation and transcription. **Caveat:** one run per setting.

### F2. Thinking helps image reading a little, at a high price in time

For images the effect reverses: thinking on raised or kept every model's score, but multiplied the
time per image.

<!-- generated:f_thinking_vision -->
| Suite | Model | Correct, thinking off | Correct, thinking on | Seconds per image, off | Seconds per image, on |
|---|---|---|---|---|---|
| vision-v1 | gemma4:12b-mlx | 21/25 | 23/25 | 0.51 | 6.33 |
| vision-v1 | gemma4:26b-mlx | 22/25 | 25/25 | 7.06 | 19.09 |
| vision-v1 | gemma4:e4b-mlx | 20/25 | 23/25 | 0.32 | 3.98 |
| vision-v1 | qwen3.8:27b-mlx | 24/25 | 25/25 | 2.24 | 6.75 |
| vision-v2 | gemma4:12b-mlx | 9/10 | 10/10 | 0.36 | 4.03 |
| vision-v2 | gemma4:26b-mlx | 9/10 | 10/10 | 0.97 | 33.57 |
| vision-v2 | gemma4:e4b-mlx | 8/10 | 9/10 | 0.3 | 7.36 |
| vision-v2 | qwen3.8:27b-mlx | 10/10 | 10/10 | 3.9 | 8.37 |
<!-- /generated:f_thinking_vision -->

**What to do:** keep thinking off for quick looks; turn it on only when an image must be read exactly
and a wait is acceptable.

### F3. More reasoning effort did not make a better coding agent

gpt-oss at high effort solved fewer tasks than at low or medium, and took longer.

<!-- generated:f_effort_coding -->
| Suite | gpt-oss reasoning effort (with extensions) | Tasks solved | Minutes per suite |
|---|---|---|---|
| v1 | low | 3/5 | 3.5 |
| v1 | medium | 4/5 | 7.1 |
| v1 | high | 1/5 | 11.3 |
| v2 | low | 3/5 | 7.3 |
| v2 | medium | 5/5 | 17.8 |
| v2 | high | 3/5 | 39.0 |
<!-- /generated:f_effort_coding -->

### F4. One mid-size model beat two small ones on real file jobs

Routing chat to a fast small model (e4b) and file work to a tool model (gpt-oss) passed fewer family
jobs than one 27B Qwen or one 12B Gemma doing everything, and was not faster per job. Each small
model alone did worse still.

<!-- generated:f_one_vs_two -->
| Setup (no harness) | Family jobs | Assistant, English | Assistant, Japanese/French | Seconds per family job |
|---|---|---|---|---|
| qwen3.8 27B alone | 10/12 of 12 | 14/14 | 8/8 of 8 | 29.8 |
| gemma4 12B alone | 12/12/12/12/11 of 12 | 14/14/14/14 of 14 | 8/7/8/8 of 8 | 21.02 |
| e4b for chat + gpt-oss for files | 8/7 of 12 | 14/13 of 14 | 6/6/5 of 8 | 19.1 |
| gpt-oss 20B alone | 6/12 | 14/14 | 7/8 | 26.5 |
| gemma4 e4b alone | 6/12 | 12/14 | 7/8 | 8.8 |
<!-- /generated:f_one_vs_two -->

**What to do:** for an assistant that touches files, prefer one capable model over a split.
**Caveat:** 12 jobs per run; one job is about 8 points.

### F5. A 12B model matched the 27B on assistant work, but it cannot code or hear

gemma4:12b passed as many family jobs and assistant requests as qwen3.8:27b, answered faster, and
takes less memory. It is not a general replacement: it solved few coding tasks, and its speech
recognition was far behind Whisper and even behind the smaller e4b.

<!-- generated:f_12b -->
|  | gemma4:12b | qwen3.8:27b |
|---|---|---|
| Family jobs | 12/12/12/12/11 of 12 | 10/12 of 12 |
| Assistant, English | 14/14/14/14 of 14 | 14/14 |
| Assistant, Japanese/French | 8/7/8/8 of 8 | 8/8 of 8 |
| Seconds per family job | 21.02 | 29.8 |
| Coding, no extensions | 3/15 (v1, v2, v3, latest run each) | 14/15 (v1, v2, v3, latest run each) |
| Speech to text | 34.1% errors | cannot hear |
| Images (vision-v1, thinking off) | 21/25 | 24/25 |
| Memory once loaded | 8.4 GB | not yet measured |
<!-- /generated:f_12b -->

### F6. The harness lifts weak models, and one rule backfired on a strong one

Each harness version added rules and checks after mistakes we saw: **v2** checks that a claimed
change really changed a file, and sends the model back when it answered without looking; **v3** adds
rules (use the next-best source, compute totals with a tool, answer in the asker's language) and gives
the model the family's folder as its home; **v3.1** adds one rule after the 12B case below.

<!-- generated:f_harness -->
| Family jobs passed | No harness | v2 | v3 | v3.1 |
|---|---|---|---|---|
| gpt-oss 20B | 6/12 | 7/6 of 12 | 8/8 of 12 | — |
| e4b + gpt-oss | 8/7 of 12 | 7/9 of 12 | 9/10 of 12 | — |
| qwen3.8 27B | 10/12 of 12 | 12/12 of 12 | 12/12 of 12 | 12/12 |
| gemma4 12B | 12/12/12/12/11 of 12 | — | 11/11 of 12 | 11/12/12/12/12 of 12 |
<!-- /generated:f_harness -->

gpt-oss and the pair gained from each version. Qwen was already near the top. The 12B, at or near full marks
without the harness, dropped a job with v3: asked to "clean up my old folder", it moved every file
out without asking, in both runs. v3 had told the model to do move and sort requests at once "with
sensible choices"; the 12B read "clean up" as a sort. v3.1 says that vague clean-up requests can mean
deleting, so the model must list, suggest and ask first. With v3.1 the 12B asked before touching
the folder in both runs and qwen kept its full score; the 12B's remaining miss was a different job
(a detail left out of a French agenda entry).

**What to do:** test every harness change on every model you might use, including the ones it was
not written for.

### F7. Coding extensions help weak tool users, not strong ones

Repairing broken tool calls lifted gemma4:26b from almost nothing to a working agent. For gpt-oss and
qwen, which already call tools well, the extensions scored the same as pi with no extensions or a
little lower.

<!-- generated:f_extensions -->
| Suite | Model | No extensions | With extensions (setup: tasks solved) |
|---|---|---|---|
| v1 | gemma4:26b-mlx | 1/0 of 5 | guard: 3/5 |
| v1 | gpt-oss:20b | 4/5 | flat: 4/4/4 of 5 |
| v1 | qwen3.8:27b-mlx | 4/4 of 5 | guard: 4/5; flat: 4/5 |
| v2 | gemma4:26b-mlx | — | guard2: 5/5 |
| v2 | gpt-oss:20b | 5/5 | flat: 4/4/5 of 5 |
| v2 | qwen3.8:27b-mlx | 5/5 | guard2: 5/5; flat: 4/5 |
| v3 | gemma4:26b-mlx | — | guard: 3/5 |
| v3 | gpt-oss:20b | 5/5 | tuned: 5/4/4 of 5 |
| v3 | qwen3.8:27b-mlx | 5/5 | guard: 4/5; flat: 5/5 |
<!-- /generated:f_extensions -->

### F8. A self-review pass did not make qwen a better coder

After each task, coding-boost can ask the model to review its own change as four reviewers
(contract, correctness, validation, scope). On qwen it fixed one task, lost others, and took longer.

<!-- generated:f_review -->
| Suite | qwen, extensions | qwen, extensions + self-review | Minutes, without review | Minutes, with review |
|---|---|---|---|---|
| v1 | 4/5 | 4/4 of 5 | 7.4 | 14.15 |
| v2 | 4/5 | 5/5 of 5 | 39.5 | 30.95 |
| v3 | 5/5 | 4/3 of 5 | 43.5 | 46.65 |
<!-- /generated:f_review -->

### F9. More tries rarely paid

Two or four tries per task, keeping the best, or two agents revising each other's work, took two to
five times longer and scored about the same as one plain run.

<!-- generated:f_tries -->
| Suite | Setup | What it does | Tasks solved | Minutes per suite |
|---|---|---|---|---|
| v1 | flat | final extensions: as auto, plus a flat edit tool for gpt-oss | 4/4/4 of 5 | 7.13 |
| v1 | att2 | two tries per task at once, keep the first that passes the visible tests | 4/2 of 5 | 11.0 |
| v1 | att2pick | two tries, keep the one passing most model-written tests | 4/5 | 33.5 |
| v1 | pair | two runs at once that each see the other's work and revise (2 rounds) | 4/5 | 32.5 |
| v2 | flat | final extensions: as auto, plus a flat edit tool for gpt-oss | 4/4/5 of 5 | 15.47 |
| v2 | att2 | two tries per task at once, keep the first that passes the visible tests | 0/5 of 5 | 15.6 |
| v2 | att2pick | two tries, keep the one passing most model-written tests | 5/5 | 59.0 |
| v2 | pair | two runs at once that each see the other's work and revise (2 rounds) | 5/5 | 79.2 |
| v3 | tuned | same extensions as flat (named tuned on the held-out v3 suite) | 5/4/4 of 5 | 29.83 |
| v3 | att2pick | two tries, keep the one passing most model-written tests | 3/5 | 73.8 |
| v3 | att4 | four tries, keep the first that passes | 4/5 | 44.3 |
<!-- /generated:f_tries -->

### F10. A specialised speech model beats chat models that hear

Whisper made far fewer errors than any Gemma model that accepts audio, in every language. Noise hurt
French most for every model.

<!-- generated:f_speech -->
| Model (thinking off) | Error % (all clips) | English | Japanese | French | French, noisy |
|---|---|---|---|---|---|
| whisper-large-v3-mlx | 7.8 | 2.5 | 3.0 | 5.3 | 26.1 |
| whisper-large-v3-turbo | 8.9 | 2.9 | 3.2 | 7.0 | 36.0 |
| whisper-medium-mlx | 10.5 | 2.5 | 5.2 | 8.1 | 36.7 |
| whisper-small-mlx | 16.5 | 2.5 | 9.5 | 17.5 | 46.7 |
| gemma4:e4b-mlx | 17.5 | 2.5 | 8.9 | 15.6 | 47.9 |
| gemma4:12b-mlx | 34.1 | 8.2 | 42.5 | 31.7 | 57.5 |
| whisper-base-mlx | 37.0 | 2.9 | 21.6 | 38.5 | 112.2 |
<!-- /generated:f_speech -->

### F11. The smallest models translated best

Translation of short chat messages did not improve with size: the small Gemma models led, and were
also the fastest.

<!-- generated:f_translation -->
| Model (thinking off; gpt-oss at low) | chrF | Seconds per message |
|---|---|---|
| gemma4:e4b-mlx | 73.8 | 0.22 |
| gemma4:26b-mlx | 73.3 | 0.55 |
| gemma4:e2b-mlx | 72.0 | 0.16 |
| gemma4:12b-mlx | 71.5 | 0.52 |
| gpt-oss:20b | 68.8 | 1.85 |
| qwen3.8:27b-mlx | 68.3 | 1.13 |
<!-- /generated:f_translation -->

### F12. Cheap rules route most messages; a small model handles the rest

Rules alone ("names a file", "has an attachment", tool words) sent most messages the right way with
no model call. Asking e4b for one word (CHAT or TOOLS) on the rest did best. A small dedicated
classifier was faster but less accurate.

<!-- generated:f_routing -->
| Router | How | Right |
|---|---|---|
| laya-router-mlx | rules+simple | 43/53 |
| laya-router-mps | rules+simple | 43/53 |
| ollama-gemma4-e4b-mlx | both | 47/53 |
| ollama-gemma4-e4b-mlx | model | 47/53 |
| ollama-gemma4-e4b-mlx | rules | 36/53 |
<!-- /generated:f_routing -->

### F13. Plan memory by what is loaded, not by the download; switching models is cheap

A model takes much more memory once loaded than its download size, because of the context cache and
runtime buffers. Two models that "fit" by download size can push a 32 GB Mac into swap: when that
happened during a bench, an app crashed and the run scored lower (that run is set aside).
Switching models, on the other hand, was cheap: loading a stopped model and getting its first word
took seconds, not minutes, so swapping a butler model for a builder model when the house is quiet
costs little. These loads were measured right after the models had been used, so their files were
likely still in macOS's file cache; a load from a cold disk would take longer.

<!-- generated:f_memory -->
| Model | Download (GB) | Loaded (GB) | Context (tokens) | Seconds to load and answer one word |
|---|---|---|---|---|
| gemma4:12b-mlx | 7.7 | 8.4 | 8192 | 2.3 |
| gemma4:e4b-mlx | 9.5 | — | — | 3.3 |
| gpt-oss:20b | 13.0 | 13.0 | 8192 | 5.8 |
| muse-glimmer:30b-nvfp4-dflash | 19.0 | 19.4 | 8192 | — |
| qwen3.6:35b-mlx | 23.0 | 23.3 | 8192 | — |
| qwen3.8:27b-dflash2-nvfp4 | 19.0 | 20.1 | 8192 | — |
| qwen3.8:27b-mlx | 18.0 | — | — | 6.0 |
| qwen3.8:27b-mtp-q4_K_M | 17.0 | 18.9 | 8192 | — |
<!-- /generated:f_memory -->

### F14. Where a reply's time goes, and whether parallel requests help

The test sends a system prompt about the size of mini's (rules plus tool list) and one everyday
question. Three things show in the table:

- **Ollama's prompt cache works when the system prompt stays the same.** Sending the same system
  prompt again cuts reading time to almost nothing on the MLX builds. The multi-token-prediction
  build reads the prompt again every time.
- **Terse answers cut writing time the most.** Writing is most of a reply's time, and a two-sentence
  answer writes a fraction of the tokens.
- **Changing the system prompt throws the cache away.** The terse row adds one line to the system
  prompt, so it pays the full reading cost again. A terse rule belongs in the fixed system prompt.

gpt-oss reads prompts fast with or without the cache. qwen3.8:27b-mlx failed with a server error in
this run and is being measured again.

<!-- generated:f_speed -->
| Model | Request | Prompt tokens read | Seconds reading | Tokens written | Seconds writing | Total seconds |
|---|---|---|---|---|---|---|
| gemma4:12b-mlx | first request | 4828 | 7.46 | 425 | 13.9 | 21.36 |
| gemma4:12b-mlx | same system prompt again | 4830 | 0.12 | 424 | 13.08 | 13.21 |
| gemma4:12b-mlx | asked for a terse answer | 4838 | 7.58 | 52 | 1.79 | 9.37 |
| gpt-oss:20b | first request | 4444 | 0.2 | 388 | 10.27 | 10.49 |
| gpt-oss:20b | same system prompt again | 4446 | 0.06 | 567 | 15.04 | 15.13 |
| gpt-oss:20b | asked for a terse answer | 4452 | 0.15 | 103 | 2.71 | 2.88 |
| qwen3.8:27b-dflash2-nvfp4 | first request | 4715 | 15.82 | 653 | 37.4 | 53.23 |
| qwen3.8:27b-dflash2-nvfp4 | same system prompt again | 4717 | 0.26 | 345 | 19.92 | 20.19 |
| qwen3.8:27b-dflash2-nvfp4 | asked for a terse answer | 4724 | 16.57 | 64 | 3.57 | 20.15 |
| qwen3.8:27b-mtp-q4_K_M | first request | 4715 | 3.01 | 502 | 40.36 | 43.37 |
| qwen3.8:27b-mtp-q4_K_M | same system prompt again | 4717 | 2.81 | 548 | 42.72 | 45.54 |
| qwen3.8:27b-mtp-q4_K_M | asked for a terse answer | 4724 | 2.85 | 57 | 5.59 | 8.44 |
| qwen3.6:35b-mlx | first request | 4715 | 2.67 | 353 | 5.41 | 8.08 |
| qwen3.6:35b-mlx | same system prompt again | 4717 | 0.17 | 503 | 7.56 | 7.74 |
| qwen3.6:35b-mlx | asked for a terse answer | 4724 | 2.71 | 50 | 0.87 | 3.59 |
| muse-glimmer:30b-nvfp4-dflash | first request | 4396 | 15.6 | 327 | 22.12 | 37.72 |
| muse-glimmer:30b-nvfp4-dflash | same system prompt again | 4398 | 0.12 | 336 | 22.98 | 23.1 |
| muse-glimmer:30b-nvfp4-dflash | asked for a terse answer | 4404 | 0.3 | 195 | 11.45 | 11.76 |

Several requests at once, each writing exactly 256 tokens:

_Measurement queued. A first try let each reply run to its own length, so totals could not be compared; it is set aside._
<!-- /generated:f_speed -->

### F15. The graders needed fixing too

Reading failures one by one found three grader bugs, each marking a right answer wrong: "3-month"
written with a hyphen, "let me know" as a way of asking before deleting, and "May 17 (moved from
May 3)" read as the old date. They were fixed and the affected runs re-graded from the saved replies;
`runs.json` keeps the original verdicts. Small benches need their failures read, not only counted.

## Recommendations for a 32 GB Mac

These follow from the findings above; the in-progress work below may change them.

- **Household assistant that also works on files:** one capable model rather than a split
  ([F4](#f4-one-mid-size-model-beat-two-small-ones-on-real-file-jobs)). qwen3.8:27b if it must also code;
  gemma4:12b if it need not and memory or speed matter more
  ([F5](#f5-a-12b-model-matched-the-27b-on-assistant-work-but-it-cannot-code-or-hear)).
- **Speech:** Whisper large-v3-turbo, not a chat model that hears ([F10](#f10-a-specialised-speech-model-beats-chat-models-that-hear)).
- **Translation:** gemma4:e4b with thinking off ([F1](#f1-thinking-makes-translation-and-speech-recognition-worse-and-much-slower), [F11](#f11-the-smallest-models-translated-best)).
- **Coding agent:** qwen3.8:27b or gpt-oss:20b, thinking off, with no extensions or self-review
  ([F3](#f3-more-reasoning-effort-did-not-make-a-better-coding-agent), [F7](#f7-coding-extensions-help-weak-tool-users-not-strong-ones), [F8](#f8-a-self-review-pass-did-not-make-qwen-a-better-coder)).
- **Images:** thinking off by default, on when exactness matters ([F2](#f2-thinking-helps-image-reading-a-little-at-a-high-price-in-time)).
- **Any harness change:** re-run it on every candidate model ([F6](#f6-the-harness-lifts-weak-models-and-one-rule-backfired-on-a-strong-one)).

## In progress

- Speed: how a reply's time splits between reading the prompt and writing the answer, the effect of
  Ollama's prompt cache and of terse answers, qwen3.8:27b-mlx again, and 1, 2 and 4 requests at once with equal reply lengths (F14).
- Faster builds of Qwen 3.8 (a drafter build and a multi-token-prediction build), the Qwen 3.6
  mixture-of-experts model, Meta's Muse-Glimmer-30B and NVIDIA's Nemotron 3.5 Lightning, on the same
  benches.

## Methods and limits

- **Small suites.** 5 coding tasks, 8 to 14 assistant requests, 12 family jobs. One task can be the
  whole difference between two setups; look at the repeats, and treat single runs as indications.
- **One machine, in normal use.** Other apps were running. Speeds on another Mac will differ; the
  rankings mostly should not.
- **Set-aside runs.** Runs made while the Mac was swapping, stopped part way, on an older version of a
  test, or diagnostic, are kept in `runs.json` with the reason and left out of every table.
- **Model tags** are this Mac's Ollama names. MLX-runner models answer one request at a time, so rows
  marked "3 at once" measure queueing, not more throughput.
- **The assistant benches drive DropBox's real assistant code** (`mini.py`) against a stand-in server,
  so the harness under test is the one that ships.

## All results

### Download size

<!-- generated:sizes -->
| Model | Download size (GB) |
|---|---|
| gemma4:e4b-mlx | 9.5 |
| gpt-oss:20b | 13.0 |
| qwen3.8:27b-mlx | 18.0 |
| gemma4:26b-mlx | 18.0 |
| gemma4:12b-mlx | 7.7 |
<!-- /generated:sizes -->

### Coding agent: models

Three suites of 5 tasks each; a task counts only when the hidden tests pass. v3 was held out while
the extensions were tuned.

<!-- generated:coding -->
| Suite | Model | Setup | Tasks solved (each run) | Minutes per run |
|---|---|---|---|---|
| v1 | gemma4:26b-mlx | guard | 3/5 | 4.6 |
| v1 | gemma4:26b-mlx | off | 1/0 of 5 | 30.05 |
| v1 | gemma4:e4b-mlx | guard | 1/5 | 2.8 |
| v1 | gpt-oss:20b | flat | 4/4/4 of 5 | 7.13 |
| v1 | gpt-oss:20b | off | 4/5 | 7.7 |
| v1 | qwen3.8:27b-mlx | guard | 4/5 | 4.6 |
| v1 | qwen3.8:27b-mlx | guard2-terse | 4/5 | 4.6 |
| v1 | qwen3.8:27b-mlx | off | 4/4 of 5 | 6.6 |
| v2 | gemma4:26b-mlx | guard2 | 5/5 | 22.4 |
| v2 | gemma4:e4b-mlx | guard | 1/5 | 2.5 |
| v2 | gpt-oss:20b | flat | 4/4/5 of 5 | 15.47 |
| v2 | gpt-oss:20b | off | 5/5 | 15.3 |
| v2 | qwen3.8:27b-mlx | guard2 | 5/5 | 15.7 |
| v2 | qwen3.8:27b-mlx | guard2-terse | 5/5 | 15.1 |
| v2 | qwen3.8:27b-mlx | off | 5/5 | 20.7 |
| v3 | gemma4:26b-mlx | guard | 3/5 | 20.6 |
| v3 | gemma4:e4b-mlx | guard | 0/5 | 5.0 |
| v3 | gpt-oss:20b | tuned | 5/4/4 of 5 | 29.83 |
| v3 | qwen3.8:27b-mlx | guard | 4/5 | 23.7 |
| v3 | qwen3.8:27b-mlx | off | 5/5 | 23.5 |
<!-- /generated:coding -->

### Coding agent: gpt-oss:20b strategies

<!-- generated:coding_strategies -->
| Suite | Setup | What it does | Solved (each run) | Minutes |
|---|---|---|---|---|
| v1 | att2 | two tries per task at once, keep the first that passes the visible tests | 4/2 of 5 | 11.0 |
| v1 | att2esc | att2 + escalate to high effort if both fail | 4/5 | 12.8 |
| v1 | att2pick | two tries, keep the one passing most model-written tests | 4/5 | 33.5 |
| v1 | auto | as cut, plus 'auto' effort: low for quick replies, default once a tool runs | 2/5 | 8.7 |
| v1 | cut | guard + coding-boost; cut-off replies are continued at once | 4/3 of 5 | 9.05 |
| v1 | esc | flat + one more run at high effort when visible tests still fail | 4/5 | 6.9 |
| v1 | flat | final extensions: as auto, plus a flat edit tool for gpt-oss | 4/4/4 of 5 | 7.13 |
| v1 | guard | pi + local-model-guard (repairs broken tool calls, stops loops) | 3/5 | 6.0 |
| v1 | high-ext | extensions, reasoning effort high | 1/5 | 11.3 |
| v1 | low-ext | extensions, reasoning effort low | 3/5 | 3.5 |
| v1 | medium-ext | extensions, reasoning effort medium | 4/5 | 7.1 |
| v1 | pair | two runs at once that each see the other's work and revise (2 rounds) | 4/5 | 32.5 |
| v2 | att2 | two tries per task at once, keep the first that passes the visible tests | 0/5 of 5 | 15.6 |
| v2 | att2esc | att2 + escalate to high effort if both fail | 5/5 | 30.7 |
| v2 | att2pick | two tries, keep the one passing most model-written tests | 5/5 | 59.0 |
| v2 | auto | as cut, plus 'auto' effort: low for quick replies, default once a tool runs | 5/5 | 22.5 |
| v2 | cut | guard + coding-boost; cut-off replies are continued at once | 4/5 of 5 | 23.35 |
| v2 | esc | flat + one more run at high effort when visible tests still fail | 5/5 | 10.1 |
| v2 | flat | final extensions: as auto, plus a flat edit tool for gpt-oss | 4/4/5 of 5 | 15.47 |
| v2 | high-ext | extensions, reasoning effort high | 3/5 | 39.0 |
| v2 | low-ext | extensions, reasoning effort low | 3/5 | 7.3 |
| v2 | medium-ext | extensions, reasoning effort medium | 5/5 | 17.8 |
| v2 | mini | the prompt mini (DropBox's assistant) uses | 3/5 | 15.6 |
| v2 | pair | two runs at once that each see the other's work and revise (2 rounds) | 5/5 | 79.2 |
| v3 | att2pick | two tries, keep the one passing most model-written tests | 3/5 | 73.8 |
| v3 | att4 | four tries, keep the first that passes | 4/5 | 44.3 |
| v3 | bare | pi with no extensions (same as off) | 5/5 | 30.4 |
| v3 | tuned | same extensions as flat (named tuned on the held-out v3 suite) | 5/4/4 of 5 | 29.83 |
<!-- /generated:coding_strategies -->

### Assistant and family jobs

<!-- generated:assistant -->
| Test | Model | Setup | Passed (each run) | Seconds per request |
|---|---|---|---|---|
| assistant-en | gemma4:12b-mlx | one model | 14/14/14/14 of 14 | 7.0 |
| assistant-en | gemma4:12b-mlx | one model, 3 at once | 14/14 | 18.8 |
| assistant-en | gemma4:12b-mlx | one model, harness v3 | 14/14 | 12.1 |
| assistant-en | gemma4:12b-mlx | one model, harness v3.1 | 14/14/14 of 14 | 14.57 |
| assistant-en | gemma4:26b-mlx | one model | 13/14 | 11.4 |
| assistant-en | gemma4:e4b-mlx | one model | 12/14 | 2.5 |
| assistant-en | gemma4:e4b-mlx | one model, 3 at once | 14/14 | 6.8 |
| assistant-en | gemma4:e4b-mlx | routed to gpt-oss:20b | 14/13 of 14 | 4.75 |
| assistant-en | gemma4:e4b-mlx | routed to gpt-oss:20b, 3 at once | 14/14 | 10.4 |
| assistant-en | gemma4:e4b-mlx | routed to gpt-oss:20b, harness v2 | 13/14 | 4.6 |
| assistant-en | gemma4:e4b-mlx | routed to gpt-oss:20b, harness v3 | 14/14 | 8.9 |
| assistant-en | gpt-oss:20b | one model | 14/14 | 6.5 |
| assistant-en | gpt-oss:20b | one model, 3 at once | 14/14 | 13.8 |
| assistant-en | qwen3.6:35b-mlx | one model, harness v3.1 | 14/14 | 58.5 |
| assistant-en | qwen3.8:27b-dflash2-nvfp4 | one model, harness v3.1 | 14/14 | 16.0 |
| assistant-en | qwen3.8:27b-mlx | one model | 14/14 | 10.2 |
| assistant-en | qwen3.8:27b-mlx | one model, harness v2 | 14/14 | 10.8 |
| assistant-en | qwen3.8:27b-mlx | one model, harness v3 | 14/14 | 15.1 |
| assistant-en | qwen3.8:27b-mtp-q4_K_M | one model, harness v3.1 | 14/14 | 80.2 |
| assistant-lang | gemma4:12b-mlx | one model | 8/7/8/8 of 8 | 8.62 |
| assistant-lang | gemma4:12b-mlx | one model, harness v3 | 8/8 | 20.1 |
| assistant-lang | gemma4:12b-mlx | one model, harness v3.1 | 7/8/8 of 8 | 18.73 |
| assistant-lang | gemma4:26b-mlx | one model | 7/8 | 13.6 |
| assistant-lang | gemma4:e4b-mlx | one model | 7/8 | 3.0 |
| assistant-lang | gemma4:e4b-mlx | routed to gpt-oss:20b | 6/6/5 of 8 | 5.87 |
| assistant-lang | gemma4:e4b-mlx | routed to gpt-oss:20b, 3 at once | 7/8 | 13.4 |
| assistant-lang | gemma4:e4b-mlx | routed to gpt-oss:20b, harness v2 | 5/8 | 5.6 |
| assistant-lang | gemma4:e4b-mlx | routed to gpt-oss:20b, harness v3 | 6/8 | 10.8 |
| assistant-lang | gpt-oss:20b | one model | 7/8 | 8.7 |
| assistant-lang | qwen3.6:35b-mlx | one model, harness v3.1 | 6/8 | 16.3 |
| assistant-lang | qwen3.8:27b-dflash2-nvfp4 | one model, harness v3.1 | 7/8 | 10.7 |
| assistant-lang | qwen3.8:27b-mlx | one model | 8/8 of 8 | 13.0 |
| assistant-lang | qwen3.8:27b-mlx | one model, harness v2 | 8/8 | 12.6 |
| assistant-lang | qwen3.8:27b-mlx | one model, harness v3 | 8/8 | 12.1 |
| assistant-lang | qwen3.8:27b-mtp-q4_K_M | one model, harness v3.1 | 8/8 | 61.8 |
| family-jobs | gemma4:12b-mlx | one model | 12/12/12/12/11 of 12 | 21.02 |
| family-jobs | gemma4:12b-mlx | one model, harness v3 | 11/11 of 12 | 17.5 |
| family-jobs | gemma4:12b-mlx | one model, harness v3.1 | 11/12/12/12/12 of 12 | 16.0 |
| family-jobs | gemma4:e4b-mlx | one model | 6/12 | 8.8 |
| family-jobs | gemma4:e4b-mlx | routed to gpt-oss:20b | 8/7 of 12 | 19.1 |
| family-jobs | gemma4:e4b-mlx | routed to gpt-oss:20b, 3 at once | 8/12 | 24.9 |
| family-jobs | gemma4:e4b-mlx | routed to gpt-oss:20b, harness v2 | 7/9 of 12 | 24.1 |
| family-jobs | gemma4:e4b-mlx | routed to gpt-oss:20b, harness v3 | 9/10 of 12 | 26.95 |
| family-jobs | gpt-oss:20b | one model | 6/12 | 26.5 |
| family-jobs | gpt-oss:20b | one model, harness v2 | 7/6 of 12 | 22.8 |
| family-jobs | gpt-oss:20b | one model, harness v3 | 8/8 of 12 | 29.1 |
| family-jobs | muse-glimmer:30b-nvfp4-dflash | one model, harness v3.1 | 12/12 | 79.0 |
| family-jobs | qwen3.6:35b-mlx | one model, harness v3.1 | 10/8 of 12 | 158.4 |
| family-jobs | qwen3.8:27b-dflash2-nvfp4 | one model, harness v3.1 | 12/12 of 12 | 19.75 |
| family-jobs | qwen3.8:27b-mlx | one model | 10/12 of 12 | 29.8 |
| family-jobs | qwen3.8:27b-mlx | one model, harness v2 | 12/12 of 12 | 29.1 |
| family-jobs | qwen3.8:27b-mlx | one model, harness v3 | 12/12 of 12 | 27.45 |
| family-jobs | qwen3.8:27b-mlx | one model, harness v3.1 | 12/12 | 25.5 |
| family-jobs | qwen3.8:27b-mtp-q4_K_M | one model, harness v3.1 | 12/12 of 12 | 167.5 |
<!-- /generated:assistant -->

### Routing

<!-- generated:routing -->
| Router | How | Right of 53 labelled requests |
|---|---|---|
| laya-multilingual-mlx | fine | 36/53 |
| laya-multilingual-mlx | rules+fine | 39/53 |
| laya-multilingual-mlx | rules+simple | 37/53 |
| laya-multilingual-mlx | simple | 35/53 |
| laya-router-mlx | fine | 37/53 |
| laya-router-mlx | rules+fine | 40/53 |
| laya-router-mlx | rules+simple | 43/53 |
| laya-router-mlx | simple | 41/53 |
| ollama-gemma4-e4b-mlx | both | 47/53 |
| ollama-gemma4-e4b-mlx | model | 47/53 |
| ollama-gemma4-e4b-mlx | rules | 36/53 |
<!-- /generated:routing -->

### Translation

<!-- generated:translation -->
| Model | Setup | chrF, en/ja/fr (higher is better) | Seconds per message |
|---|---|---|---|
| gemma4:e4b-mlx | thinking off | 73.8 | 0.22 |
| gemma4:26b-mlx | thinking off | 73.3 | 0.55 |
| gemma4:e2b-mlx | thinking off | 72.0 | 0.16 |
| gemma4:12b-mlx | thinking off | 71.5 | 0.52 |
| gemma4:e4b-mlx | thinking on | 70.8 | 2.52 |
| gpt-oss:20b | thinking low | 68.8 | 1.85 |
| gpt-oss:20b | thinking medium | 68.4 | 3.85 |
| qwen3.8:27b-mlx | thinking off | 68.3 | 1.13 |
| qwen3.8:27b-mlx | thinking on | 66.7 | 22.45 |
| gpt-oss:20b | thinking high | 63.2 | 42.26 |
| gemma4:12b-mlx | thinking on | 55.3 | 154.74 |
<!-- /generated:translation -->

### Speech to text

Error is words wrong for English and French, characters wrong for Japanese.

<!-- generated:speech -->
| Model | Error % (all) | en | ja | fr | fr, noisy | Language guessed right | Seconds of compute per second of audio |
|---|---|---|---|---|---|---|---|
| whisper-large-v3-mlx | 7.8 | 2.5 | 3.0 | 5.3 | 26.1 | 99% | 0.186 |
| whisper-large-v3-turbo | 8.9 | 2.9 | 3.2 | 7.0 | 36.0 | 98% | 0.111 |
| whisper-medium-mlx | 10.5 | 2.5 | 5.2 | 8.1 | 36.7 | 99% | 0.1 |
| whisper-small-mlx | 16.5 | 2.5 | 9.5 | 17.5 | 46.7 | 98% | 0.04 |
| gemma4:e4b-mlx (thinking off) | 17.5 | 2.5 | 8.9 | 15.6 | 47.9 | 92% | 0.084 |
| gemma4:e4b-mlx (thinking on) | 20.7 | 1.3 | 12.0 | 18.0 | 47.4 | 93% | 0.344 |
| gemma4:12b-mlx (thinking off) | 34.1 | 8.2 | 42.5 | 31.7 | 57.5 | 87% | 2.845 |
| gemma4:12b-mlx (thinking on) | 35.9 | 5.8 | 42.3 | 39.1 | 50.9 | 85% | 9.237 |
| whisper-base-mlx | 37.0 | 2.9 | 21.6 | 38.5 | 112.2 | 95% | 0.019 |
<!-- /generated:speech -->

### Images

<!-- generated:vision -->
| Suite | Model | Setup | Correct | Seconds per image |
|---|---|---|---|---|
| vision-v1 | gemma4:12b-mlx | thinking off | 21/25 | 0.51 |
| vision-v1 | gemma4:12b-mlx | thinking on | 23/25 | 6.33 |
| vision-v1 | gemma4:26b-mlx | thinking off | 22/25 | 7.06 |
| vision-v1 | gemma4:26b-mlx | thinking on | 25/25 | 19.09 |
| vision-v1 | gemma4:e4b-mlx | thinking off | 20/25 | 0.32 |
| vision-v1 | gemma4:e4b-mlx | thinking on | 23/25 | 3.98 |
| vision-v1 | glm-ocr:latest | thinking off | 20/25 | 0.3 |
| vision-v1 | qwen3-vl:8b-instruct | thinking off | 23/25 | 8.22 |
| vision-v1 | qwen3.8:27b-mlx | thinking off | 24/25 | 2.24 |
| vision-v1 | qwen3.8:27b-mlx | thinking on | 25/25 | 6.75 |
| vision-v2 | gemma4:12b-mlx | thinking off | 9/10 | 0.36 |
| vision-v2 | gemma4:12b-mlx | thinking on | 10/10 | 4.03 |
| vision-v2 | gemma4:26b-mlx | thinking off | 9/10 | 0.97 |
| vision-v2 | gemma4:26b-mlx | thinking on | 10/10 | 33.57 |
| vision-v2 | gemma4:e4b-mlx | thinking off | 8/10 | 0.3 |
| vision-v2 | gemma4:e4b-mlx | thinking on | 9/10 | 7.36 |
| vision-v2 | glm-ocr:latest | thinking off | 8/10 | 0.26 |
| vision-v2 | qwen3-vl:8b-instruct | thinking off | 8/10 | 8.85 |
| vision-v2 | qwen3.8:27b-mlx | thinking off | 10/10 | 3.9 |
| vision-v2 | qwen3.8:27b-mlx | thinking on | 10/10 | 8.37 |
<!-- /generated:vision -->

## Reproduce

```sh
make setup                                    # .venv with the bench dependencies
ollama pull gpt-oss:20b                       # and any model above
.venv/bin/python agent_bench.py run --bench agentic-v1 --name gpt-oss-20b-off-r1 \
    --model ollama/gpt-oss:20b --thinking off --timeout 900
.venv/bin/python dropbox_bench.py mini --model ollama/qwen3.8:27b-mlx
.venv/bin/python dropbox_bench.py family --model ollama/qwen3.8:27b-mlx
.venv/bin/python dropbox_bench.py translate --model gemma4:e4b-mlx --think off
.venv/bin/python dropbox_bench.py captions                       # Whisper; --ollama MODEL --think on|off for a model that hears
.venv/bin/python route_bench.py
.venv/bin/python vision_bench.py --help
.venv/bin/python scripts/collect_local_ai.py                     # refresh the data, this document and its web page
```

Benches take `.bench.lock`, so only one GPU job runs at a time. The assistant and family benches
take `--mini-py` to test another version of DropBox's assistant.
