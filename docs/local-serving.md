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
translation, larger for code. The router sets it from measured profiles. Quality at each setting
must be measured by the benchmark before it becomes a default; speed alone is not a reason.

## Nightly benchmark (macOS)

Regressions in a model's behaviour are invisible to ordinary CI, which has no model. To watch yours,
run the benchmark every night on the machine that serves it. This uses `launchd` on your own Mac,
not a CI runner: a self-hosted runner on a public repository would run code from pull requests.

```sh
mkdir -p ~/.local/share/deputy ~/Library/LaunchAgents
cp examples/nightly-bench.sh ~/.local/share/deputy/
cp examples/deputy-nightly-bench.plist ~/Library/LaunchAgents/dev.deputy.nightly-bench.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.deputy.nightly-bench.plist
```

It runs at 03:00, skips (and says so in `skipped.log`) when the backend does not answer, and keeps
one report per night in `~/.local/state/deputy/nightly/` for 90 days. Reports never block anything:
compare two nights with `deputy bench --compare BEFORE.json AFTER.json`. Remove it with `launchctl bootout gui/$(id -u)/dev.deputy.nightly-bench`.

Comparing prints the pass rate, time per task and estimated saving of both reports, names the tasks
that failed more (or less) and says if the served model changed. It exits 0 unless you add
`--fail-on-regression`, so a cron job can alert on it. A task that failed more times, or a pass rate
that fell by more than 5 points, counts as a regression; per-task detail is limited to failure counts
because a report keeps no per-task pass rate.
