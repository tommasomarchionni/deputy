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
- Draft length 3 and a smaller micro-batch did not help.
- Prefill is slow (about 60 tokens/s), so large inputs cost minutes; chunking and not re-reading
  matter more than any flag.

## Consequence for deputy

Reasoning is a **per-task-class parameter**, not a global one: off or small for extraction and
translation, larger for code. Setting it per kind of task is planned; today it is one server flag.
Quality at each setting
must be measured by the benchmark before it becomes a default; speed alone is not a reason.

Regressions in a model's behaviour are invisible to ordinary CI, which has no model. Run `deputy bench` by hand when the served model, its server options or deputy's version change, because the measured profile records which model it was measured on.
