# Memory

deputy keeps what was **measured**, not a diary. A model that is not trained on your project
forgets everything between calls; what is worth keeping across calls is what a measurement wrote.

## What is kept

- **The measured model profile**, per backend and model: pass rate, turns, speed, and which
  served model it was measured on. Written by `deputy bench`, never by a model. It sets the turn
  limit, feeds routing, and `status` says how old it is and whether the served model changed.
- **The trace** (`runs.jsonl`): one line per run with metadata, check outcomes, metrics, the
  saving estimate and your `feedback`; never file contents or answers. `deputy stats` and the
  `not_worth_delegating` flag read it.
- **The runs of the local agent**: the patch, the result and the drafts of each run, removed
  after a week.

All of it is JSON on your machine, in the state directory (`~/.local/state/deputy`). Workers and
the local agent cannot write it, and it holds no secrets, tokens or file contents.

## What was removed, and why

Until 0.21 deputy also had *lessons* (short reminders added to the worker prompt where a kind of
check kept failing), *project memory* (pointers to a repository's rule files, added to the
worker prompt) and an offline optimiser of the worker profile. All three were built with tests
and then judged by their use: of 476 recorded runs none carried a lesson and one carried a
project note, and the optimiser, run twice, found no gain on the held-out tasks. Code that is not
used still costs reading and maintenance, so they were removed. A memory feature comes back only
with a measurement that shows a run with it beats the same run without it.
