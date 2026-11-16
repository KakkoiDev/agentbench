# Feature to implement: custom bench runs in the lab queue

Status: **to implement**. Today's stopgap is `script_queue.py` (see below).

## Problem

The lab app's queue only runs a fixed set of whitelisted jobs (`lab_app/actions.py`,
`build_command`). Real experiments need options those jobs cannot express, so they run
from shell scripts instead (for example `results/queue-20261116b.sh`):

- **agent_bench.py**: several pi extensions from any pi-core checkout or worktree
  (`--extension a.ts,b.ts`), environment knobs (`PI_GUARD_GPT_OSS_EFFORT=auto`,
  `PI_GUARD_FLAT_EDIT`...), `--terse`, `--attempts N`, `--escalate EFFORT`,
  `--escalate-model MODEL`, `--pair`, `--bench agentic-v2`, run names.
- **Parallel runs**: `with_parallel_ollama.py` (isolated Ollama with `OLLAMA_NUM_PARALLEL`,
  unloading the model from the system server first), wrapping several runs.
- **glm_bench.py**: other prompt files (`benches/coding-v2.json`), styles (`normal,terse`)
  and configs per model.
- **vision_bench.py**: `run --bench vision-v1|v2 --think off|on --unload` and `coload`.

Scripts work, but the lab cannot reorder, pause, remove or estimate their steps, and
before `script_queue.py` it did not even know they existed ("Up next: 0 jobs" while ten
steps waited).

## Stopgap in place

`script_queue.py plan NAME TITLE "step|est_s" ...` publishes a script's steps, and
`script_queue.py run NAME "step" -- cmd ...` marks each one running, done or failed. The
lab lists the steps not done yet as read-only items in Up next and the queue page
("Run by a script: stop it there"), and hides them once the script ends.

## What to build

1. **New job kinds** in `actions.build_command`, each validated like the existing ones:
   - `agentic-custom`: bench (`agentic-v1|v2`), model (installed Ollama), thinking, run
     name (SAFE), extensions (only `index.ts` files under `~/Code/pi-core*`), env knobs
     from an allow-list with allowed values, and the flags `terse`, `attempts` (1–4),
     `escalate` (low|medium|high), `escalate_model` (installed), `pair`.
   - `parallel`: a list of `agentic-custom` jobs run through `with_parallel_ollama.py`
     with `parallel` 2–4 slots.
   - `coding-custom`: prompts file under `benches/`, styles, configs, model.
   - `vision`: bench, model, think off|on, plus `coload`.
2. **Campaigns**: a JSON file under `campaigns/` (an ordered list of these jobs) that the
   lab can queue in one click, replacing the shell scripts. Each entry keeps its own
   estimate, and the queue shows them like any other job (reorder, remove, pause).
3. **Progress for every kind**: agentic progress counts the tasks of its own bench (v1 and
   v2 differ; today the card says "x/5" from agentic-v1), counts tries for `attempts` and
   `pair`, and vision shows answered/total.
4. **Results**: compare views understand the new run names (try twice, pair, escalate,
   terse) and group them with their baseline.
5. Retire `script_queue.py` once campaigns cover what scripts do, or keep it for one-off
   scripts.

## Done when

- The 2026-11-16 queue (terse coding, vision for five models, try twice, pair, try twice
  plus retry) can be queued from the lab with no shell script, and shows estimates, order
  and live progress.
- Invalid values (an unknown extension path, an uninstalled model, a bad env value) are
  refused with a clear message, and no arbitrary command can be queued.
- Tests cover the validation of each new kind.
