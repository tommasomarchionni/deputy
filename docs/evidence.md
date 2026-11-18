# Evidence

What deputy saves and what it costs in quality, measured on 2026-11-18 with the current version.
Savings use the yardstick described in [configuration](configuration.md#how-the-saving-is-estimated):
bytes divided by four on both sides, so a difference is an order of magnitude, not an invoice.

- Worker: `Jackrong/Qwen3.8-27B-MTP-GGUF:Q4_K_M`, served locally by llama.cpp on a laptop.
- Frontier references: **Claude Opus 5.5** (`claude-opus-5-5`) and **Claude Sonnet 5.5**
  (`claude-sonnet-5-5`), each recorded separately.

## Everyday tasks on two real repositories

21 tasks of the kind an agent hands off during ordinary work, each with checks: 12 on a private
multi-app TypeScript and Python repository (some of its documents are in Italian), 9 on this
repository. Inventories, summaries with cited lines, a section list translated from Italian, git
history, code questions, counts, searches and one-line lookups. One correction round allowed. The
set was run twice, with the current worker profile and tools on the second run.

**Quality.** First run: all 21 answers passed their checks, and Claude Opus 5.5 compared every
answer with the files by hand, line numbers included: all 21 were correct, one cited range off by
one line. Second run: 20 passed; the 21st was correct but failed a check written with a line limit
that was out of date (the file had grown). `cites_lines` now takes the file's `path` and reads its
current length, so that check cannot go stale.

**Saving**, net of what the orchestrator paid for the request and the result:

| Kind of task | Tasks | Saved, run 1 | Saved, run 2 | Worker time per task |
| --- | ---: | ---: | ---: | ---: |
| Reading: summaries, inventories, translated sections, history, code questions | 11 | 52,751 | 61,767 | about 2 minutes |
| Lookups and searches: one line, a commit subject, which files mention X | 10 | 2,400 | 10,833 | 30 to 60 s |

A reading task saves about 4,800 to 5,600 frontier tokens. The lookups' figure varies because it
counts what the worker happened to read: on the second run it read more around a few short answers.
What an orchestrator would read for a lookup is a line or two either way.

Reading pays; lookups and searches do not, because the orchestrator would see the same short
result itself. That is the rule in the delegation guide, and why a task that names only small files
is flagged `small_input`.

## The worker profile

The 10 fast tasks of the built-in suite, 3 runs each (30 runs per profile), on the local worker:

| Profile | Passed | Citations correct | Mean time | Output tokens |
| --- | ---: | ---: | ---: | ---: |
| Every tool named, one phrase each (the bundled profile) | 100 % | 100 % | 23.5 s | 265 |
| Same, with three tools named | 100 % | 100 % | 28.3 s | 320 |
| Same as the first, plus seven rules from public prompt collections | 96.7 % | 83 % | 27.0 s | 304 |

Telling a small model which tools it has makes it faster at equal quality. Adding rules made this
27B model worse, so the profile stays short; those ideas went into the orchestrator's guide instead.

On the same tasks the frontier models, one pass each by hand: Claude Opus 5.5 passed 10 of 10
(about 9,380 tokens to read and answer), Claude Sonnet 5.5 passed the first 8 of 8 (about 9,270).
Delegating them costs the orchestrator only the request and the result.

## Worker tools that pay for themselves

Each measured on the local worker, with and without the tool:

| Tool | Task | Without | With |
| --- | --- | --- | --- |
| `list_files` | how many note files, and which is the largest | 1 of 3 passed, 127 s | 3 of 3, 14 s |
| `summarise_log` | which worker logged most errors in a 2,400-line log | 2 of 2, 69 s | 2 of 2, 29 s |
| read-only `git` | which commit changed a rule, the old value, and why | not possible | 3 of 3, 49 s |

`summarise_log` keeps every distinct kind of line: a 2,400-line log of about 34,500 tokens becomes
about 120, and a real 215-line CI log keeps all 176 kinds of line in about 3,000 tokens instead of
7,700. A search with many hits lists every matching file with its count: 66 hits in 29 files cost
about 490 tokens.

## Where an agent's reading goes, and rtk

`deputy discover` on 11 Claude Code sessions of one real repository: about 5.2 million tokens of
tool results read by the orchestrator. Reads (files, searches, listings) were 62 %, and large reads,
over about 1,000 tokens each, 30 % of everything: that is what delegation can take. Command output
(git, gh, test runners) was 15 %.

That 15 % is what [rtk](https://github.com/rtk-ai/rtk) (1.2.0, run unmodified) compresses. On
read-only commands of two real repositories:

| Command | Raw | With rtk | Saved |
| --- | ---: | ---: | ---: |
| `git diff` over 5 commits | 139,031 | 8,182 | 94 % |
| `git log -30` | 2,451 | 843 | 65 % |
| `ls -la` of a source folder | 661 | 148 | 77 % |
| `gh run list` | 583 | 219 | 62 % |
| `grep -rn`, `pytest -q`, `ruff check` | 4 to 2,305 | same | 0 % |

The two tools cut different parts of the bill and do not interfere: rtk shortens what shell
commands print; deputy takes long reads off the orchestrator, and its worker never uses the shell.

## What this does not show

- Savings are estimates with one yardstick on both sides; real tokenizers differ by up to about a
  quarter, and prompt caching changes what a re-read costs.
- 21 tasks on two repositories, two runs; the profile and tool comparisons have 2 or 3 runs.
  The numbers show direction and order of magnitude.
- The frontier references are one pass by hand, not repeated measurements.
- Worker time is not free for you: a local 27B model needs one to three minutes for a long document.
- Answers are only as checked as their checks; Claude Opus 5.5 reviewed every answer of both runs
  against the files they were given.

A nightly benchmark runs on the maintainer's machine ([local serving](local-serving.md#nightly-benchmark-macos));
`deputy bench --compare` shows what changed between two nights.
