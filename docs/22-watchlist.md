# Watchlist: things to check again later

Upcoming changes that could make this Mac mini M6 faster, with the date to look again. When one
lands, rerun `lab.py speed` on the main models and update this list.

## December 2026: M6 neural accelerators in MLX (up to ~2x prompt reading)

The M6 GPU has neural accelerators reachable through Metal 4 tensor ops. Measured on a 12-core M6
([MLX #4627](https://github.com/ml-explore/mlx/issues/4627)): fp16 13.6 TF/s, int8 26.9 TOPS (1.98x),
fp8 about 29-31 TOPS. Stock MLX turns quantized weights back into fp16 first, so prompt reading is
capped at the fp16 rate.

- [MLX #4627](https://github.com/ml-explore/mlx/issues/4627) and [MLX #4802](https://github.com/ml-explore/mlx/pull/4802):
  use the 8-bit lanes. Unmerged on 2026-11-19; promise up to about 2x prompt reading.
- [MLX #4781](https://github.com/ml-explore/mlx/pull/4781): merged 2026-11-01, not yet released.
  Quantized matmul over a few rows 1.3-2.6x faster; helps multi-token prediction and draft checks.
- [mlx-lm #990](https://github.com/ml-explore/mlx-lm/pull/990): multi-token prediction in stock mlx-lm.

Why it matters here: the assistant reads a system prompt of about 4,700 tokens on every request,
so faster prompt reading speeds up every model. Ollama's MLX runner picks these up after MLX releases.

## Also to recheck

- ds4 and SlotStream: both had large November updates; pull them before benchmarking.
- llama.cpp MoE on-demand paging ([RFC #23324](https://github.com/ggml-org/llama.cpp/discussions/23324)) and
  mlx-lm expert offload: unmerged in November 2026.
