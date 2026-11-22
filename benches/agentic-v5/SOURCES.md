# agentic-v5 sources and licenses

All 15 tasks come from **Aider's polyglot benchmark**
(https://github.com/Aider-AI/polyglot-benchmark, commit `7e0611e77b54e2dea774cdc0aa00cf9f7ed6144f`,
2024-12-22), the Python exercises in `python/exercises/practice/`. Aider picked these Exercism
exercises as the hardest ones for frontier models (https://aider.chat/2024/12/21/polyglot.html).

The polyglot repository has no license file of its own; its README says every exercise is
copyright © Exercism and comes from Exercism's language tracks. The Python exercises come from
https://github.com/exercism/python (**MIT License**, Copyright (c) 2021 Exercism), and their
test data from https://github.com/exercism/problem-specifications (**MIT License**). Both
licenses were checked on GitHub on 2026-11-22. The MIT License allows copying and modifying with
this notice kept:

> Permission is hereby granted, free of charge, to any person obtaining a copy of this software
> and associated documentation files (the "Software"), to deal in the Software without
> restriction, including without limitation the rights to use, copy, modify, merge, publish,
> distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
> Software is furnished to do so, subject to the following conditions: The above copyright notice
> and this permission notice shall be included in all copies or substantial portions of the
> Software. THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND.

The exercise files are copied unchanged into `exercises/<name>/` (only `.meta/template.j2` and
`wordy/.approaches/`, which are not used, were left out). `tasks.py` builds each task from them:
the instruction is `.docs/introduction.md` + `instructions.md` + `instructions.append.md` plus one
line naming the file and asking for `python3 -m unittest` to pass; the starting files are the stub
and `test_<name>.py`, the official test file with all but 2-3 tests removed; the hidden tests are
the official `<name>_test.py` unchanged (official test names kept); the reference fix is
`.meta/example.py`. Per-exercise authors are in each `.meta/config.json`.

| task | license | hidden tests | Exercism author(s) | original idea |
| --- | --- | ---: | --- | --- |
| forth | MIT (Exercism) | 54 | cmccandless | |
| poker | MIT (Exercism) | 37 | MartinDelille | Udacity CS212 |
| bowling | MIT (Exercism) | 31 | aes421 | Uncle Bob's Bowling Game Kata |
| sgf-parsing | MIT (Exercism) | 23 | cmccandless | |
| grep | MIT (Exercism) | 25 | behrtam | Cornell CS3110 |
| wordy | MIT (Exercism) | 25 | betegelse | Extreme Startup |
| book-store | MIT (Exercism) | 20 | behrtam | Cyber-Dojo Harry Potter kata |
| react | MIT (Exercism) | 14 | cmccandless | |
| pov | MIT (Exercism) | 15 | cmccandless | 4clojure |
| zipper | MIT (Exercism) | 14 | cmccandless | |
| dominoes | MIT (Exercism) | 13 | cmccandless | |
| go-counting | MIT (Exercism) | 11 | yunchih | |
| two-bucket | MIT (Exercism) | 9 | parthsharma2 | Wolfram water pouring problem |
| rest-api | MIT (Exercism) | 9 | cmccandless | |
| transpose | MIT (Exercism) | 12 | nithin-vijayan | r/dailyprogrammer #270 |
| **total** | | **312** | | |

## Considered and left out

- **zebra-puzzle** (polyglot): only 2 tests, and the answer is widely memorized.
- **LiveCodeBench**: its problems are AtCoder/LeetCode/Codeforces statements (not openly licensed),
  mostly stdin/stdout, and the private tests are shipped as pickled blobs in large Hugging Face
  files, which we would have to unpickle (unsafe) and download in bulk.
- **EvalPlus HumanEval+/MBPP+**: too easy for our best local models.
- **SWE-bench**: tasks need full repository checkouts and Docker images; not self-contained.

`sources/polyglot-benchmark/` is the shallow clone these files were copied from (about 30 MB);
it is not needed at run time and can be deleted.
