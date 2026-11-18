# How deputy improves

"Improves" is only real if it is **measured**. This page says what deputy does today, what it does
not do yet, and how to watch it work.

## The short answers

**Does it improve by itself as it runs?** Partly. `deputy learn` turns what keeps failing in your real
use into lessons that go into the worker prompt, and `deputy tune` changes request settings only if
they win; neither runs on its own, you run them (or see the issues for automating them). Today it improves when a measurement says so:
`deputy bench` measures your model and `deputy tune` changes request settings only if they win. While
it runs, deputy records a trace of every run and your `feedback`, but nothing reads them yet
(per task class routing is planned: [#13](https://github.com/tommasomarchionni/deputy/issues/13),
[#14](https://github.com/tommasomarchionni/deputy/issues/14),
[#18](https://github.com/tommasomarchionni/deputy/issues/18)).

**Does it improve its own code?** No, and it will never do so unattended. Code changes are made by
people (or by an agent working through the normal issue and pull-request process), and the benchmark
and the tests are what find the defects. Real examples from this project: the benchmark reported a
correct answer as failed, which exposed a gap in the citation grader (fixed, with tests); a property
test found a bug in the settings writer (fixed). The optimiser (`deputy optimise`) *proposes* a better profile with a
benchmark report; a human decides.

**When can I see it?** Now. Run `deputy tune` against your model (a few minutes with a fast model,
about a quarter of an hour with a slow local one). See [Watch it work](#watch-it-work).

## What works today, and what is planned

| What | Status |
| --- | --- |
| Self-correction inside a run: checks, one retry with the exact failure, hand back (`needs_frontier`) | **today** |
| Benchmark of your model on a built-in suite (`deputy bench`, `--json`) | **today** |
| Tuning of request settings, recommended or applied (`deputy tune`, `auto_tune`, `--revert`) | **today** |
| Measured model profile written after every benchmark, and read back: the turn limit follows it, and `status` says how old it is and whether the model changed | **today** |
| Trace of every run and your feedback, and a report from it (`deputy stats`) | **today** (the report reads it; lessons and routing do not yet) |
| Advice that a kind of task does not pay off (`not_worth_delegating`), from the trace | **today** (a flag and a line in `status`; the orchestrator decides) |
| Lessons learned from real use (`deputy learn`): short reminders in the worker prompt where a kind of check keeps failing, with evidence and expiry | **today** (derived from the trace) |
| Each lesson tested against the benchmark, with and without it, and removed if it hurts (`deputy lessons check`) | **today** |
| Measured profiles read by routing: the backend is chosen per task by pass rate and speed | **today** ([configuration](configuration.md#routing)); per task class is planned |
| Offline optimiser that proposes a better worker profile, judged on the benchmark and confirmed on held-out tasks, applied only on your approval, with rollback (`deputy optimise`) | **today** for the profile text; other parameters are covered by `tune` |
| Benchmark on your own folder with your own tasks, from a TOML file (`deputy bench --tasks-file`), writes rolled back | **today** |
| Nightly benchmark on the machine that serves your model, one JSON report per night (`examples/nightly-bench.sh`, scheduled with launchd) | **today**, opt-in |

## How it works today

### Inside a run: self-correction

The server runs the worker, then the checks you declared. A failing check gets one correction round
with the exact failure; if it still fails the result is `checks_failed` with `needs_frontier`, and
the orchestrator does the task. Bounded retries, bounded turns.

### Between runs: measure, then change settings

- `deputy bench` runs a synthetic project that is identical every time. Graders are code. Tasks:

  | Task | What it proves | Grader |
  | --- | --- | --- |
  | `cited-extract` | finds the facts and cites their lines | `contains`, `cites_lines` |
  | `needle-in-long-file` | finds one line in a 600-line log (paging or `grep`) | `contains`, `cites_lines` (exact line) |
  | `write-file` | writes exactly what was asked | `file_written`, `file_contains` |
  | `refuse-secret` | does not leak a planted value from a hidden file | `excludes` |
  | `short-summary` | stays under a length limit | `max_chars` |
  | `short-with-facts` | a short answer that still holds the facts | `contains`, `max_chars` |
  | `translate-keep-terms` | translates and keeps numbers and identifiers | `file_written`, `file_contains` |
  | `write-passing-test` | writes a test that really passes (opt-in, runs code) | `file_contains`, `command` |
  | `count-in-huge-file` | aggregates over a 140 KB log, larger than the context (opt-in, slow) | `contains` |

  `write-passing-test` runs the model's code on your machine (in a temporary directory, with a
  minimal environment and a 30 second limit), so it only runs with `--run-code`. `count-in-huge-file` takes minutes on a local model, so it only
  runs with `deputy bench --slow` (never in `tune` or in lesson checks).
  It reports pass rate, citation accuracy, turns, output tokens, seconds with spread, tokens per
  second and the estimated tokens saved for the orchestrator, and saves a **model profile**.
- `deputy tune` runs the suite for your settings and for candidates (thinking off, shorter output,
  cooler sampling) and recommends one only if it loses no accuracy and is clearly cheaper than the
  spread. Server flags it cannot change (context size, thinking budget) are printed as a hint.
- `deputy optimise` is a reflective prompt search for the worker profile. The worker reads where the
  current profile failed on the benchmark and proposes rewrites (one that fixes the failures, one
  that is shorter). Each is measured on the selection tasks; one is kept only if it loses no
  accuracy and gains accuracy or is clearly cheaper, and then it must also hold up on the held-out
  tasks. It writes a report and a candidate file and changes nothing: `deputy optimise --apply ID`
  is your approval, `--revert ID` puts the previous profile back. The proposal text comes from a
  model, so read the diff in the report before applying; the sandbox does not depend on it.
- **Who decides** is `auto_tune`: `off`, `propose` (default) or `apply`. Applied settings go to a
  separate `tuned.toml`; `deputy tune --revert` undoes them.

## Watch it work

```sh
deputy bench                 # one pass: a table of how your model does
deputy tune                  # baseline plus candidates; recommends (or applies, if you allow it)
deputy tune --apply          # write the winner to tuned.toml now
deputy tune --revert         # undo it
```

A real `deputy tune` run on a laptop (Qwen3.8 27B, Q4_K_M, local llama.cpp; the five original
tasks, one pass):

```text
variant         pass  cites  turns  tok out     sec    +-  tok/s  saved*
baseline        100%   100%    1.8      203    19.8  10.6   10.3    1742
thinking-off    100%   100%    1.8       92    15.5   8.5    6.0    1622
short-output    100%   100%    1.8      185    20.1  10.9    9.2    1746
cool            100%   100%    1.8      240    19.2  11.7   12.5    1621

recommendation: thinking-off (same accuracy, 15.5s vs 19.8s)
to apply it: deputy tune --apply
```

Reading it: every variant passes everything, so none is worse; turning thinking off produces 55%
fewer output tokens and is faster, so it is recommended. The spread (`+-`, seconds) is as large as
the gain, which is why a single pass is a hint, not a proof: repeat with `--runs 3` before applying.
The suite is easy, so it cannot say whether thinking-off hurts harder tasks; that is what the
planned larger suite is for. Your numbers will differ; that is the point of measuring.

## Guardrails

Nothing changes without a measurement; a human gate on anything that is not a request setting; one
command to roll back; no unattended edits of code or prompts; limits on retries and turns; no private
data in the benchmark.

## The benchmark design

The suite covers extraction, paging, writing, refusing, summarising, translating and writing a
test, with repetitions, a spread for every metric and a held-out split (below). A frontier baseline
is planned ([#8](https://github.com/tommasomarchionni/deputy/issues/8)). The design:

- Tasks from real work: cited extraction, required-facts coverage, code that must pass a test,
  translation with terms and numbers preserved, a file larger than the context, a task that must
  be refused (write outside the sandbox, ask for a shell).
- **Deterministic graders** (code, not a model). A model judge only for style, and never alone.
- N repetitions per task (temperature is not zero), report mean and spread.
- A held-out set never used to tune, to avoid fitting the benchmark.
- Metrics: `tokens_saved` for the orchestrator (the headline number), pass rate, citation accuracy, fact coverage, turns, tokens, wall-clock, escalations.
- A frontier baseline on synthetic tasks only, never with private code, and only when allowed.

## Reading the spread, and the held-out tasks

A model is not deterministic, so one pass proves little. `deputy bench --runs N` repeats the suite N
times and reports, for the pass rate, turns, output tokens and seconds, the mean and the spread
(the population standard deviation: `±` on the pass rate is in percentage points, `±tok` and
`±sec` are in tokens and seconds).

```text
variant          pass  cites  turns  tok out  ±tok     sec  ±sec  tok/s  saved*
baseline      80%±10   100%    2.4      912     85    61.5    7.2   14.8    1530
thinking-off  80%±10   100%    2.2      311     40    24.0    3.1   13.0    1530
```

How to read it: if two variants differ by less than their spread, the difference is noise, not
signal. Here the pass rate is the same, but thinking-off produces a third of the tokens in 40% of the
time and the gap (37 s) is far larger than the spread (7 s), so it is real. With `--runs 1` there is
no pass-rate spread to show; use at least `--runs 3` before you trust a close call.

**Held-out tasks.** `deputy tune` picks a setting on one set of tasks (the *selection*) and only
then confirms it on tasks the choice never saw (`needle-in-long-file` and `translate-keep-terms`,
fixed in the code). A setting that merely suits the tasks it was judged on fails the confirmation
and the recommendation is withdrawn, so it is never applied. If you narrow the run with `--tasks`
so that no held-out task is left, the recommendation says it was not confirmed.

## Measuring on your own repository

The built-in suite is synthetic. To see whether delegation pays for *your* work, write the tasks you
actually give an agent in a TOML file and run them on your folder:

```sh
deputy bench --tasks-file examples/bench-tasks.toml --workdir /path/to/your/repo
```

Each `[[task]]` has a `name`, the `task` text and optional `checks` (the same fields as the
`delegate` tool). Tasks run one after another; whatever the worker writes is rolled back after each
one, so the folder is left as it was. The table shows, per task, the result, turns, seconds and the
estimated tokens the orchestrator did not have to read; a short task can be negative, which is the
answer to "should I delegate this". Nothing leaves your machine except the calls to your own backend.

## Where your orchestrator's reading goes: `deputy discover`

```sh
deputy discover --project /path/to/your/repo --days 30
```

It reads the Claude Code session transcripts of that folder on your machine (nothing is sent
anywhere) and sums the text of every tool result, by category: **reads** (file reads, searches,
listings, and shell commands such as `sed`, `cat`, `grep`), **command output** (git, gh, test
runners, build tools) and the rest. A result over 4,000 characters (about 1,000 tokens) is counted
as *large*: that is where a delegation can pay. The last lines give two upper bounds: the large
reads a delegation could take, and the command output that a command-output compressor such as
[rtk](https://github.com/rtk-ai/rtk) works on. Images are ignored. These are volumes, not costs:
prompt caching makes a re-read cheaper, and a read made to edit a file cannot be delegated.

Measured on 11 sessions of one real repository: large reads were 30 % of everything the orchestrator
read, command output 15 %.
