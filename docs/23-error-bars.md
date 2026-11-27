# Comparing two setups: paired differences with error bars

Until November 2026 most comparisons here were single runs on a few items ("3/5 solved vs 2/5"). That is
mostly noise. From 2026-11-27 a comparison between two setups (two models, a harness change, exact vs lossy
streaming) is reported as a paired difference with an error bar, after Miller, "Adding Error Bars to Evals"
(Anthropic, [arXiv:2411.00640](https://arxiv.org/abs/2411.00640)), the method
[livenerf](https://github.com/ninjahawk/livenerf) uses.

```bash
.venv/bin/python scripts/compare_runs.py --a 'results/RUNS-OF-A*' --b 'results/RUNS-OF-B*' --labels A B
```

- **Items, not runs, are the unit.** Each item (a mini case, an expert job, a coding task) gets a score per run
  (pass 1/0, expert points/out_of, hidden tests passed/total), averaged over that setup's runs. The difference
  B - A is taken item by item, so item difficulty drops out, and its standard error is over items.
- **The interval uses Student's t** for the number of items: with 5 items, 1.96 standard errors would claim
  far more certainty than there is.
- **Repeat runs and alternate the setups.** Repeats make each item's score less noisy; alternating A, B, A, B
  means drift over time (thermal state, other load) hits both.
- **Output tokens are a second signal** for coding tasks: reported as a ratio with its own interval. A setup
  that thinks less or more shows there before its score moves.
- **Pick informative items.** Items every setup always solves, or never solves, carry no signal. For A/B work
  prefer the items a model only sometimes gets right, run several times.

What it showed on earlier claims (single runs): Inkling MLX against GGUF on hard coding, +37 ± 23 points, not
significant (95% CI -26 to +100, 5 tasks); Qwen 3.8 against Inkling MLX, +16 ± 19, not significant.
