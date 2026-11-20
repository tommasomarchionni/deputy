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

The 10 fast tasks the built-in suite had at the time, 3 runs each (30 runs per profile), on the local
worker:

| Profile | Passed | Citations correct | Mean time | Output tokens |
| --- | ---: | ---: | ---: | ---: |
| Every tool named, one phrase each (the bundled profile) | 100 % | 100 % | 23.5 s | 265 |
| Same, with three tools named | 100 % | 100 % | 28.3 s | 320 |
| Same as the first, plus seven rules from public prompt collections | 96.7 % | 83 % | 27.0 s | 304 |

Telling a small model which tools it has makes it faster at equal quality. Adding rules made this
27B model worse, so the profile stays short; those ideas went into the orchestrator's guide instead.

The whole suite today (11 fast tasks with the code task, including CI-log triage, 3 runs each, 33
runs): 100 % passed, 100 % of citations correct, 34.6 s per task on average. The code-outline task,
added after that run, passed 3 of 3 on its own.

The frontier references, one pass each by hand: Claude Opus 5.5 passed all 12 fast tasks, Claude
Sonnet 5.5 the first 8 of 8. On the two newest tasks Opus needed only about 120 to 150 tokens,
because one search found the answer; see the limits below.

## Live checks through the MCP server

deputy 0.16 driven by Claude Code on a real repository, each answer checked against the files:

| Task | Result |
| --- | --- |
| Why did a real CI run fail (log fetched with `gh` into a file) | correct: job, step, cause, and that a second job failed only as a consequence |
| How many ADR files, and the largest (`list_files`) | correct |
| What the last three commits changed (read-only `git`) | correct, with file and line counts |
| A five-bullet summary of an Italian document section, within 1,800 characters | handed back: 1,913 characters even after the correction round, so the check refused it |
| One version number from a tiny file | correct, but it should have been flagged `small_input`; fixed since |

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

`deputy discover` on 11 Claude Code sessions and 250 subagents of one real repository: about 18.9
million tokens of tool results read by the agents. Reads (files, searches, listings) were 71 %, and
large reads, over about 1,000 tokens each, 50 % of everything: that is what delegation can take.
Command output (git, gh, test runners) was 13 %.

That 13 % is what [rtk](https://github.com/rtk-ai/rtk) (1.2.0, run unmodified) compresses. On
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

## A local agent for code changes

Measured on 2026-11-20 on an M4 Pro with 64 GB: eight small changes to this repository (code,
tests and documentation each), given as one request to Claude Code 2.1.321 with Claude Sonnet 5.5
as the frontier model. The local model is Qwen3.8 27B (Q4_K_M) on llama.cpp. Hidden checks, one
per change, grade the result; the weights are those of the bill (input 1, cache write 1.25, cache
read 0.1, output 5).

| Who makes the changes | Frontier tokens, weighted | Cost | Time | Result |
| --- | ---: | ---: | ---: | --- |
| The frontier model alone (two runs) | 133,556 and 140,080 | $0.34 and $0.38 | 2.5 min | 8 of 8; tests, linter, formatter and type checker pass |
| The frontier model hands them to the local agent in 8 steps, waits with `deputy wait`, reviews and applies the patch | 77,660 | $0.22 | 88 min | 8 of 8; the same gates pass after the frontier reformatted one file |

- **About 43 % fewer frontier tokens, for 35 times the time.** Of the 77,660, about 30,800 are
  the prompt cache written again after 88 idle minutes: a session that keeps working meanwhile
  does not pay them.
- **The local model needs short conversations.** The same eight changes in one conversation:
  7 of 8 after 105 minutes, stuck on the last one, a third of the time spent reading its own
  history again. In eight steps, directly (no frontier): 8 of 8 in 79 minutes, no repair round,
  documentation updated in five files.
- **Handing over has to be said with the request.** With the rule only in the server's
  instructions the frontier model delegated as its first move in 3 of 6 runs; with the hook's
  line next to the request, in 6 of 6.
- **A smaller, faster model is not better here.** Qwen3.6-35B-A3B made the eight changes pass in
  45 minutes with one repair round, but updated the documentation in one place of four and left
  two tests that did not test their change.

- **On a real issue of a JavaScript monorepo it did the mechanical part and not the hard one.**
  The issue had been closed by a frontier subagent (Sonnet 5.5, together with a second small
  issue: 96 requests, 1.94 million weighted tokens, 24 minutes for the two). Qwen3.8 27B started
  from the commit before that change, with the issue as three steps and the unit tests, the type
  check and the formatter as checks. Steps one and two (dead attributes removed from three HTML
  files, ten unused strings removed from a typed catalogue, the two tests updated) were done in
  14 minutes: the four product files came out byte for byte as merged, and the merged tests
  pass on them. Step three, a new test that reads the routes and templates of four apps, was
  not: 40 tool calls without an edit, a nudge, then the 30 minutes of the step ran out, and the
  run ended as `partial` after 50 minutes. What the step had written was thrown away, which is
  why a draft is now kept.

What this does not show: one set of changes on a Python repository and one issue on a JavaScript
one, one run of each. A single small change costs less done by the frontier model itself than
handed over.

## What this does not show

- Savings are estimates with one yardstick on both sides; real tokenizers differ by up to about a
  quarter, and prompt caching changes what a re-read costs.
- 21 tasks on two repositories, two runs; the profile and tool comparisons have 2 or 3 runs.
  The numbers show direction and order of magnitude.
- The frontier references are one pass by hand, not repeated measurements.
- **The estimate assumes the orchestrator would read the whole file.** When one search finds the
  answer (the failing line of a CI log, a list of definitions), an orchestrator that searches pays
  far less: about 120 to 150 tokens on the two newest benchmark tasks. Delegation pays when the
  answer needs reading and understanding (summaries, inventories, causes that need context); when
  one search finds it, do it yourself.
- Worker time is not free for you: a local 27B model needs one to three minutes for a long document.
- Answers are only as checked as their checks; Claude Opus 5.5 reviewed every answer of both runs
  against the files they were given.

**`digest` on real files.** 17 questions on two real repositories, asked twice (34 runs), on a local
Qwen3.8 27B (Q4_K_M). Claude Opus 5.5 checked every passing answer against the source lines.

| Result | Runs |
| --- | ---: |
| Answerable questions | 30 |
| Passed the checks | 24 (80 %) |
| Passed and fully correct | 20 |
| Passed with one wrong detail | 3 |
| Passed but incomplete | 1 |
| Handed back to the orchestrator | 6 |
| Questions the files cannot answer | 4 |
| Of those, an invented answer that passed | 0 |

- **Saving:** the 24 passing answers replaced about 103,500 tokens of files with about 6,500 tokens
  of answers, about 4,000 tokens each (bytes / 4).
- **Limit:** a passing answer proves its sources, not its reading. Three of 24 had a true citation
  and a wrong detail, so read the cited line before you act on a fact.
- **Time:** a median of 109 seconds per question on this laptop.

**Where to start measuring your own use.** `deputy discover` on 30 days of real sessions of one
repository, 11 main sessions and 249 subagents: 3,572 large file reads (about 8.5 million tokens),
of which 3,148 were not followed by an edit of the file (about 7.5 million tokens), against 16
delegations: an adoption of 0.5 %. Subagents did 2,601 of those reads (about 6.5 million tokens), so
that is where a delegation can save the most.

**What those reads are worth.** The same command on the sessions of that repository active in one
week (3 main sessions, 279 subagents), in weighted tokens:

| | Requests | Weighted tokens | Share | Of it, context read again |
| --- | ---: | ---: | ---: | ---: |
| Main sessions | 17,249 | 898.5 M | 55 % | 87 % |
| Subagents that edit (207) | 23,109 | 698.9 M | 43 % | 75 % |
| Subagents that only read (72) | 1,898 | 39.0 M | 2 % | 52 % |

The main sessions carried a median of 430,000 tokens of context per request and were compacted at
about 963,000. Reading again the context above 200,000 tokens was 38 % of everything; the large
reads a delegation could have taken were at most 7 %. So on this workload the reading path is the
small lever, the size of the context the largest, and the subagents that edit (what the local
agent is for) the second. In four days of that week the hint to delegate a read was shown 253
times, 225 of them to subagents, and the sessions made 16 delegations, 1 of them from a subagent:
a hint is not enough to change what an agent does.

A nightly benchmark runs on the maintainer's machine ([local serving](local-serving.md#nightly-benchmark-macos));
`deputy bench --compare` shows what changed between two nights.
