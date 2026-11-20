# Serving a local model: what was measured

Setup: Apple M4 Pro, 64 GB, llama.cpp server, Qwen3.8-27B (MTP, Q4_K_M), one request at a time.
Task: summarise a 3 KB text in 5 bullets with line citations (same prompt every time, 2 runs).
Numbers are from one machine and few runs: they show direction, not precision.

| Configuration | Wall time | Output tokens | Generation speed |
| --- | --- | --- | --- |
| Context 262,144, prompt cache 26 GB, reasoning auto | 86-93 s | 1,183-1,192 | 13.9 tok/s |
| Context 32,768, prompt cache 2 GB, reasoning auto | 99-181 s | 1,386-3,000 | 16.6 tok/s |
| Same, MTP draft 3, micro-batch 512 | 129-174 s | 2,032-3,000 | 15.9 tok/s |
| Context 32,768, cache 2 GB, reasoning budget 300 | 44-58 s | 654-707 | 14.8 tok/s |
| Same, reasoning off | 29-37 s | 442-457 | 15.4 tok/s |

## What it says

- **A smaller context and cache help a little** (+19% generation speed; free memory 6.5 GB instead
  of 0.1 GB), not enough on their own.
- **The time is the number of output tokens.** With reasoning on, a 5-bullet summary produced 1,200
  to 3,000 tokens (once it hit the 3,000 cap). Capping reasoning is the big lever: 2-4x faster.
- Draft length 3 and a smaller micro-batch did not help in this test, where the reasoning
  decided the time; measured on its own, the draft length does matter (below).
- Prefill is slow (about 60 tokens/s), so large inputs cost minutes; chunking and not re-reading
  matter more than any flag.

## Consequence for deputy

Reasoning is a **per-task-class parameter**, not a global one: off or small for extraction and
translation, larger for code. Setting it per kind of task is planned; today it is one server flag.
Quality at each setting
must be measured by the benchmark before it becomes a default; speed alone is not a reason.

## Speculative decoding

Measured on 2026-11-20 on the same machine and model, with reasoning off and greedy decoding, so
that every setting writes the same text: three requests (a function rewritten inside 11,000
tokens of context, new code, a file written again with one change), 400 tokens each.

| `--spec-type`, `--spec-draft-n-max` | Long context | New code | File written again |
| --- | ---: | ---: | ---: |
| `none` | 12.2 tok/s | 12.8 | 12.7 |
| `draft-mtp`, 2 | 19.1 | 19.9 | 20.9 |
| `draft-mtp`, 4 | 25.1 | 26.6 | 31.0 |
| `draft-mtp`, 6 | 27.5 | 29.4 | 38.4 |
| `draft-mtp,ngram-mod`, 4 | 27.8 | 25.9 | 43.0 |
| `draft-mtp,ngram-mod`, 6 | 29.9 | 28.2 | 50.6 |

- **The text is the same to the character** in all six: speculation changes the speed, not the
  answer. With sampling, 8 and 12 were slower than 6: more drafts are thrown away.
- **Real work gains less than the test.** In the local agent's own conversations, by context
  size, `draft-mtp,ngram-mod` with 6 against `draft-mtp` with 2: 21.4 against 17.9 tok/s under
  5,000 tokens, 24.0 against 17.5 up to 20,000, 18.9 against 15.6 up to 40,000, and 14.3 against
  13.3 above (few requests). An agent writes short tool calls at temperature 1.0, where about
  half of the drafted tokens are accepted.
- **Quality on the benchmark did not move:** 34 of 36 against 35 of 36 before, the same two
  tasks that fail now and then in every report, the same citation rate.
- This is one model with its own draft head (MTP). Measure yours: start a second server on
  another port with each setting and send it the same requests.

## When to run the benchmark

Regressions in a model's behaviour are invisible to ordinary CI, which has no model. Run
`deputy bench` by hand when the served model, its server options or deputy's version change: the
measured profile records which model it was measured on, and a profile measured on another model
or while the server was busy gives wrong turn limits.

Keep the report (`deputy bench --json > report.json`) and compare two of them with
`deputy bench --compare BEFORE.json AFTER.json`: it prints the pass rate, time per task and
estimated saving of both, names the tasks that failed more or less, and says if the served model
changed. It exits 0 unless you add `--fail-on-regression`; only a pass rate that fell by more than
5 points counts as a regression (one extra failure among three repetitions is noise).
