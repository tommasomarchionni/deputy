# Evidence

What deputy saved and what it cost in quality, measured on 2026-11-18. The yardstick is the one
described in [configuration](configuration.md#how-the-saving-is-estimated): bytes divided by four
for both sides, so the *difference* is an order of magnitude, not an invoice.

Worker model: `Jackrong/Qwen3.8-27B-MTP-GGUF:Q4_K_M`, served locally (free for the orchestrator).
Frontier reference: **Claude Sonnet 5.5** (`claude-sonnet-5-5`).

## A real repository

Six read-only tasks on a private multi-app TypeScript and Python repository (about 900 lines of
CI documentation, several apps and packages), each with checks, run one after another. First run
with version 0.11, second with 0.12 (`deputy bench --tasks-file`), which returns a shorter result
and counts a search by the lines it returns.

| Task | 0.11: checks | 0.11: saved | 0.12: checks | 0.12: saved | 0.12: worker time |
| --- | --- | ---: | --- | ---: | ---: |
| Inventory of apps and packages, with cited lines | passed | 3,891 | passed | 4,095 | 88 s |
| Find every file that mentions an identifier | passed | 3,295 | **failed** | 3,690 | 359 s |
| Summarise a long CI document, with cited lines | passed | 12,976 | passed | 22,545 | 356 s |
| List the workflow files with one phrase each | passed | 11,566 | passed | 9,984 | 181 s |
| List the build targets of one project file | passed | −74 | passed | 3 | 14 s |
| Find the design notes that mention a topic | passed | 1,140 | passed | 1,275 | 48 s |
| **Total** | 6 of 6 | 32,794 | 5 of 6 | 41,592 | 1,044 s |

"Saved" is the estimated frontier tokens the orchestrator did not have to read, net of what it paid
for the request and the result. On 0.12 the mean is about 6,900 tokens a task. The smallest task
saves about nothing (it cost more than it saved on 0.11): delegate what is long to read; do small
lookups yourself, which is what the `small_input` and `not_worth_delegating` flags say.

The failed search on 0.12 was a real defect: the search stopped at 50 hits, and the identifier had
66 hits in 29 files, so files past the cap were never seen. Since then a large search lists every
matching file with its hit count (about 490 tokens for those 29 files), and the task's own check
caught the incomplete answer, so the orchestrator was told to redo it instead of trusting it.

## Quality against a frontier model

The built-in synthetic suite (8 fast tasks, graded by code), three repetitions on the local model,
and one pass by the frontier model graded with the same checks.

| Model | Pass rate | Mean time per task |
| --- | ---: | ---: |
| Local worker (24 runs) | 91.7 % (spread ±11.8 points) | 25.5 s |
| Claude Sonnet 5.5 (one pass of 8) | 100 % | not measured |

The local model failed one run of two tasks (finding a line number in a long file, and a summary
that had to name two facts within 300 characters); the frontier model passed all eight. The
frontier pass was done by hand in a session, so it is a reference, not a repeated measurement.
For the same eight tasks the frontier model paid about 9,270 tokens (prompt, files read, answer);
delegating them costs the orchestrator only the request and the result.

## Large logs for a small model

A worker that reads a big log through the model costs minutes. `summarise_log` reduces every line
to a template and reports each distinct kind of line once, with its count and line range, and drops
nothing but repetition ([tools](tools.md#large-logs-summarise_log)). The slow benchmark task (which
worker logged the most errors in a 2,400-line log), local worker, two runs each:

| | Pass | Mean time | Output tokens | Turns |
| --- | ---: | ---: | ---: | ---: |
| Before (search, then ask the model) | 2 of 2 | 68.9 s (spread 24.6 s) | 759 | 3.0 |
| After (`summarise_log` available) | 2 of 2 | 29.4 s (spread 0.2 s) | 398 | 2.5 |

The log itself shrinks from about 34,500 tokens to about 120. On a real 215-line CI log the summary
keeps all 176 distinct kinds of line in about 3,000 tokens instead of 7,700. Two runs each is a
small sample, and the saving is largest when a search cannot answer the question.

## Shell output, with and without rtk

The orchestrator also reads command output itself. The open-source tool
[rtk](https://github.com/rtk-ai/rtk) (1.2.0, run unmodified) compresses it. Measured on read-only
commands of two real repositories, estimated tokens (bytes / 4):

| Command | Raw | With rtk | Saved |
| --- | ---: | ---: | ---: |
| `git diff` over 5 commits | 139,031 | 8,182 | 94 % |
| `git log -30` | 2,451 | 843 | 65 % |
| `ls -la` of a source folder | 661 | 148 | 77 % |
| `gh run list` | 583 | 219 | 62 % |
| `grep -rn`, `pytest -q`, `ruff check` | 4 to 2,305 | same | 0 % |

The two tools cut different bills and do not interfere: rtk shortens what shell commands print,
deputy takes whole reads off the orchestrator and its worker never uses the shell. In 11 coding
sessions on one repository, shell commands were 77 % of what the orchestrator read, but nearly half
of that was `sed`, `grep` and `cat` (reading files), which rtk leaves alone; `git`, `gh` and `ls`
were about a tenth. So expect rtk to help mostly on git and test output, and deputy on long reads.
These are volumes, not costs: prompt caching changes what a re-read costs.

## What this does not show

- The saving is an estimate with one yardstick on both sides; real tokenizers differ by up to about
  a quarter.
- Six tasks on one repository, run once. The numbers show direction and order of magnitude.
- Worker time is not free for you: a local 27B model needs minutes for long documents.
- Nothing here measures the quality of answers that have no check.

The benchmark runs every night on the maintainer's machine (see
[local serving](local-serving.md#nightly-benchmark-macos)); this page is updated from those reports.
