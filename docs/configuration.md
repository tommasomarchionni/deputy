# Configuration

`deputy init` writes `~/.config/deputy/config.toml` (mode 600). Change the folder with
`DEPUTY_HOME`, or point at a file with `DEPUTY_CONFIG`. Traces go to `~/.local/state/deputy`
(`DEPUTY_STATE`). **Secrets never go in the file:** use `api_key_file` (must be mode 600) or
`api_key_env`.

```toml
default_backend = "local"
auto_tune = "propose"                # off | propose | apply, see "Tuning" below
auto_update = "apply"                # apply | notify | off, see "Updates" below
routing = "auto"                     # off | auto, see "Routing" below

[[backends]]
name = "local"
base_url = "http://127.0.0.1:8080"   # the server root; deputy calls /v1/chat/completions
model = "default"                    # sent in each request; llama.cpp ignores it
kind = "local"                       # local | hosted
api_key_file = "~/.config/deputy/key"
timeout_s = 600
max_tokens = 4096
extra_body = { temperature = 0.2 }   # merged into every request

[policy]
# Commands a `command` check may run (argv prefixes, no shell). Empty = none.
allowed_commands = [["node", "--test"], ["python", "-m", "pytest"]]
command_timeout_s = 120
run_budget_s = 900                   # the least time one delegation may take; 0 = no limit
```

## Backends

Anything that serves the OpenAI chat API with tool calling.

| Server | `base_url` | Notes |
| --- | --- | --- |
| llama.cpp `llama-server` | `http://127.0.0.1:8080` | start it with `--jinja` so tool calls work |
| Ollama | `http://127.0.0.1:11434` | `model` = the Ollama model name |
| LM Studio | `http://127.0.0.1:1234` | enable the local server |
| vLLM | `http://127.0.0.1:8000` | `--enable-auto-tool-choice` |
| Hosted (OpenAI-compatible) | the provider's root | `kind = "hosted"`, key via `api_key_env`; your files leave the machine |

Several backends can be listed; pick one per call with the `backend` argument.

## Profiles

A **profile** is the worker's standing instructions: a Markdown file whose front matter names it and
whose body becomes the worker's system prompt. The orchestrator picks one with the `profile`
argument of `delegate` (default `helper`); the `profiles` tool lists the ones available.

- **Where they live, and which wins.** deputy ships one profile, `helper`, inside the package. You
  can add your own in `~/.config/deputy/profiles/` (yours, every project) or in `.deputy/profiles/`
  inside the folder you delegate in (that project). For the same name the project's wins over yours,
  and yours over the bundled one.
- **Who creates them.** Nobody, automatically: there is no command that writes a new profile. You
  copy the template
  [`examples/project-dev.md`](https://github.com/tommasomarchionni/deputy/blob/main/examples/project-dev.md)
  and fill it in. The one exception is `deputy optimise --apply`, which installs an improved copy of
  a profile as yours (and keeps the previous one for `--revert`).
- **What else reaches the worker.** On top of the profile, deputy adds the lessons learned from use
  (`deputy learn`) and the project memory (`deputy memory`), which points to your repository's own
  rule files instead of copying them.
- **Build on a profile instead of copying it.** With `extends: helper` in the front matter, your
  profile gets the current text of `helper` followed by your own lines, so deputy's later
  improvements to its tools and rules reach you. Your `description`, `max_turns` and `max_retries`
  win; what you leave out comes from the parent. A profile may extend its own name (a project's
  `helper` that adds lines to the bundled one), and chains work. A parent that does not exist, or a
  loop, is ignored and `deputy doctor` says which profile and why.
- **Copies fall behind.** A copy of `helper` without `extends` (yours, a project's, or one installed
  by `deputy optimise --apply`) replaces the bundled one, so deputy's later improvements do not
  reach you. `deputy doctor` says when that happens. Prefer `helper` plus project memory, and write
  your own profile only for a kind of work `helper` does not cover.

The front matter can set the profile's limits:

```yaml
---
name: strict-reader
description: Reads and reports, no second chances
max_turns: 4        # 1 to 50: the most tool turns the worker may take
max_retries: 0      # 0 to 5: correction rounds after a failed check (default 1)
extends: helper     # optional: start from the current text of this profile
---
```

- **`max_turns`**: precedence, highest first: the `max_turns` argument of the call, the profile's,
  the limit derived from the measured model profile, 8.
- **`max_retries`**: `0` means a failed check hands the task back at once (`checks_failed`,
  `needs_frontier`); `2` allows up to two correction rounds, each with the exact failure, and
  stops as soon as the checks pass. Without it, one correction round, as before.
- A value that is not a whole number, or is outside its range, is **ignored** (the default
  applies) and `deputy doctor` says which profile and why.

## Policy

The orchestrator may declare `command` checks, but they only run if the argv starts with an entry
of `allowed_commands`. The worker model never runs anything.

**Time budget.** A delegation that runs out of time stops with `turn_limit` and the flag
`time_limit`, so a stuck model never blocks your agent. The budget is meant to stop a hang, not a
slow model: it is `run_budget_s` (900 s by default) or 20 times the time this model was measured to
need per task on your machine (`deputy bench`), whichever is larger. A model measured at 300 s per
task gets 6,000 s. Set `run_budget_s = 0` for no limit. For a slow local model, delegate with
`wait=false` and `collect` the result later, so your agent keeps working meanwhile.

## Tuning

`deputy bench` measures the model you configured; `deputy tune` also tries a few request-level
settings and recommends the best one that loses no accuracy.

```sh
deputy bench                 # one pass of the built-in suite, prints a table, saves a model profile
deputy bench --runs 3        # repeat: you get the spread too, and a fairer comparison
deputy bench --json          # the same, as JSON for scripts and reports
deputy bench --run-code      # also tasks whose grader runs model-written code (temp dir, 30 s)
deputy bench --slow          # also the huge-file task (140 KB, minutes on a local model)
deputy tune                  # baseline + candidates; recommends, or applies per `auto_tune`
deputy tune --apply          # write the winner to tuned.toml now
deputy tune --revert         # undo the last applied tuning
```

`auto_tune` decides what `deputy tune` may do alone: `off` changes nothing, `propose` (default)
prints the recommendation, `apply` writes it. Applied settings go to `tuned.toml` next to your
config, never into your config; they overlay the backend's `extra_body` and `max_tokens`, and
deleting the file undoes them. A candidate is chosen only if it keeps pass rate and citation
accuracy and is faster than the measured spread or uses at least 15% fewer output tokens.

**When the served model changes.** `deputy doctor` compares the model the server answers with the
one that was measured. With `auto_tune = "propose"` or `"off"` it only says what to do. With
`"apply"` it runs one quick tune (four tasks, one pass, one of them held out) and writes the new
profile and any confirmed winner. This happens only in `doctor`, never during a `delegate` call,
because it takes minutes. A model that was never measured is not re-tuned.

Server flags (context size, thinking budget, cache) are outside deputy's reach: when thinking
dominates the time, `tune` prints the flag to try instead of changing anything. Custom candidates:
`deputy bench --variant 'name={"extra_body":{"temperature":0.1},"max_tokens":512}'`.

## Language

Everything deputy ships (code, documentation, messages and the bundled profile) is in English, and
the bundled `helper` profile makes the worker answer in English. A custom profile can say otherwise.

## Updates

deputy never phones home with your data. The only request it makes is a GET of the public "latest
release" of this repository, at most once a day (cached; one hour after a failure).

- `auto_update = "apply"` (default): at server start, a newer **compatible** release is installed,
  and only after its build provenance is verified. Compatible means the same major version and no
  release since yours marked `BREAKING CHANGE` in its notes (the release workflow marks every
  release that contains a breaking change; if the list of releases cannot be read, only a patch is
  installed). A new major version is never installed without you. The new version runs from the
  next start, and the user-level skill is refreshed then.
- `auto_update = "notify"`: `deputy doctor` and the `status` tool say when a newer release exists.
  Nothing is installed.
- `auto_update = "off"`: no request at all.

`deputy update --check` only looks; `deputy update` upgrades now.

**What an update does.** It downloads the released wheel, checks where it was built, and only then
installs it:

1. the wheel is fetched from the GitHub release (and refused if it is far larger than a deputy
   release);
2. its **build provenance** is verified with `gh attestation verify`: the release workflow of this
   repository attests every wheel, so a match proves it was built there from a tagged commit and was
   not swapped afterwards;
3. if the check **fails**, nothing is installed, always;
4. if the check **cannot be done** (the GitHub CLI `gh` is missing or not logged in), a manual
   `deputy update` goes ahead and says plainly *"provenance NOT verified"* and why; add
   `--require-verified` to refuse instead. An **automatic** update (`auto_update = "apply"`) always
   requires verification: with nobody watching, it installs only what it could verify.

You can go back to any release with `DEPUTY_REF=<tag> sh install.sh`. The installer itself does not
verify provenance; to check a release by hand: `gh release download <tag> -R tommasomarchionni/deputy`
then `gh attestation verify <file>.whl -R tommasomarchionni/deputy`. Each release also carries the
provenance bundle as `<file>.whl.intoto.jsonl`, so the check works offline too:
`gh attestation verify <file>.whl --bundle <file>.whl.intoto.jsonl -R tommasomarchionni/deputy`.

## Usage report

Every run and every `feedback` is recorded in `~/.local/state/deputy/runs.jsonl` (metadata only: no
file content, no answers, no secrets). `deputy stats` turns it into a report:

```sh
deputy stats                 # the last 30 days
deputy stats --days 7        # a different window
deputy stats --json          # for scripts
deputy stats --include-bench # also the synthetic benchmark runs
```

It shows runs, the share that passed their checks (`ok`), how many were handed back to the
orchestrator (`esc`), how many needed a retry, the acceptance rate from your `feedback`, and the
estimated tokens saved (total and per run), for everything, by profile, and by **task class** (the
profile and the kinds of checks asked for, for example `helper:cites_lines+contains`). The `status`
tool carries a one-line version of the last 7 days.

Runs started by `deputy bench` and `deputy tune` are tagged `source: bench` in the trace and left out
of the report by default, so it describes real work; older trace lines without a source count as real
use (delete `runs.jsonl` for a clean start). Savings are summed only for runs that record how they
were estimated (`read` and `cost`): versions before 0.12 recorded a total from an estimate that
could count a whole file for each search hit, so their runs still count as runs but not their
saving, and the report says how many there were.

### How the saving is estimated

For each delegation deputy estimates two things with the same yardstick: what the worker read on the
orchestrator's behalf (`read`: the lines it read, the hit lines a search returned, and the whole of
a file summarised by `summarise_log` or scanned by `scan_file`),
and what the orchestrator paid (`cost`: the request it wrote, serialised, plus the result it reads,
serialised). The saving is `read - cost` for a run that ended `ok`; a run that did not (handed back,
out of turns or time, an error) spared nothing, since the orchestrator does the task itself, so it
counts as `-cost`, a loss. **It can be negative** either way: a task that reads little and
returns a lot costs more than it spares, and `not_worth_delegating` ([tools](tools.md)) is built on
that. The trace keeps all three numbers; `deputy stats` shows the per-run `read` and `cost`.

The yardstick is the size in UTF-8 bytes divided by four, rounded up. It is the usual rule of thumb
for English prose, and it is wrong by up to about a quarter either way: code and JSON are denser than
this says, other languages vary. Because both sides use it, the difference is meaningful as an order
of magnitude and a trend, not as an invoice. Not counted: the tool schemas the orchestrator carries
for the session (about 970 tokens for the whole tool list, a fixed cost with or without delegation), and
its own reasoning.

## The measured model profile

`deputy bench` and `deputy tune` save what they measured for your model (pass rate, mean turns,
seconds, and the model name the server answered with) in `~/.local/state/deputy/models/`, but only
after a run of the whole suite: a run limited with `--tasks` keeps the profile, because one task is
too noisy to set the turn limit and the time budget of every delegation. deputy
uses it in two ways:

- **Turn limit.** When a `delegate` call does not give `max_turns`, the limit is the measured mean
  number of turns times two, between 4 and 12; without a profile it is 8. An explicit `max_turns`
  always wins. A fast, reliable model gets a short leash; a slower one gets room.
- **`status`.** Next to each backend it shows `<backend> profile`: when it was measured and how it
  did, or `never measured: run deputy tune`, or, when the server now serves a different model than
  the one measured, a note saying so and to run `deputy tune` again.

## Lessons learned from use

`deputy learn` reads the trace of your **real** runs (benchmark runs never teach) and looks for a
kind of check that keeps failing for a profile: at least 5 runs that carried it, and at least 25% of
them failing. For each one it proposes a *lesson*: a short reminder from a fixed catalogue (for
example, for `contains`: "list every fact the task asks for and make sure each appears"), with the
evidence behind it.

```sh
deputy learn                 # a report: what it would learn, and why
deputy learn --apply         # save them; from now on they go into the worker prompt
deputy lessons               # list the active lessons and what you muted
deputy lessons remove helper:contains   # remove one, and never learn it again
deputy lessons check         # test each lesson against the benchmark: with it and without it
deputy lessons check --apply # remove the ones that make the benchmark worse
deputy bench --lessons       # measure the model with the lessons applied (default: without)
```

Rules that keep this safe: the text of a lesson comes only from the catalogue, never from a trace,
an answer or a file; at most 3 lessons are added to a prompt, only for the profile and the kinds of
checks the task asks for; a lesson expires after 14 days and is re-derived only while the evidence
still holds; workers cannot write lessons. Each run's trace records which lessons were applied, so
`deputy stats` and the benchmark can tell whether they help.

## Optimising the worker profile

```sh
deputy optimise                  # measure, reflect, judge, confirm; writes a report, changes nothing
deputy optimise --list           # saved proposals
deputy optimise --apply ID       # your approval: installs it as your profile (previous one kept)
deputy optimise --revert ID      # undo
```

Options: `--profile NAME` (default `helper`), `--backend`, `--runs N` (repeat each measurement).
A proposal is saved under the state directory with `report.md` (the tables and a diff). It can
only be applied if it also held up on the held-out tasks. A confirmed proposal's decision also states what it gains and
what it costs (pass rates, time per task, output tokens), and says so when it brings no gain on the
held-out tasks or is more than 25 % slower: confirmed only means it lost no accuracy. It takes minutes on a local model: it
runs the selection tasks for the current profile and for each proposal, then the held-out tasks.

## Routing

With more than one `[[backends]]`, `delegate` without a `backend` argument chooses one. Rules, in
order: never a more expensive or less private kind than the default backend (a local default only
uses local backends; a `hosted` default may fall back to local ones); local before hosted; measured
to pass (`deputy bench` or `tune` ran, at least 50% passed) before not measured before measured to
fail; the faster measured time first; then the default backend, then the order of the file. The
backends are probed (3 seconds) in that order: a silent one is skipped, and a run that ends in a
backend error moves on to the next. The choice and the reason are written to the trace as `route`.

`routing = "off"` always uses `default_backend`, with no fallback; naming a `backend` in the call
does the same for that call. With one backend nothing is probed and nothing changes.

## Project memory

A small note per repository, so the worker knows where the rules live and what your words mean.
Pointers, not copies: an entry says *which file* and *why*, so it cannot go stale.

```bash
deputy memory scan --apply       # find AGENTS.md, CLAUDE.md, .github/instructions/*.md ...
deputy memory pointer docs/design.md "how stages are leased"
deputy memory term Lease "a hold on one worker stage"
deputy memory                    # show; `export` prints the same markdown to review in a PR
deputy memory remove Lease
```

- `--repo DIR` picks the repository (default: the current folder; a folder inside a repository is
  that repository).
- The note lives in the state directory, keyed by the repository, never in the repository, and a
  worker cannot reach it.
- It is added to the worker's prompt only for tasks in that repository, capped at 1,500
  characters (up to 12 pointers and 20 terms).
- A pointer must be a real file inside the repository and not a secret file.

## Confinement of command checks

`[policy] command_wrapper` is an argv prefix that runs every `command` check inside an OS sandbox;
`deputy init --confine` writes one for your platform and `deputy doctor` reports whether commands
are confined. Details and what each platform allows: [security](security.md#confining-command-checks).

### Does a lesson help? `deputy lessons check`

A lesson is a bet, so it is tested. For each active lesson, deputy takes the benchmark tasks that
carry a check of the lesson's kind (a `max_chars` lesson: the tasks with a length limit), runs them
**without** the lesson and then with **only** that lesson (same model, same settings, `--runs N`
repetitions on both sides), and compares:

| Verdict | Meaning | What happens |
| --- | --- | --- |
| helps | passes more or cites better with it | kept |
| neutral | no difference the benchmark can see (the suite may simply be too easy for it) | kept |
| hurts | passes less or cites worse with it (a loss on either counts) | listed; `--apply` removes it and mutes it |
| untestable | no benchmark task carries that kind of check | kept |

A real run on the local Qwen3.8 27B, a `max_chars` lesson, two repetitions:
`pass 100% -> 100%, cites 100% -> 100%, tokens out 342 -> 209`. The verdict is neutral (accuracy did
not change) and the report shows what it did change: 39% fewer output tokens. The report is the
point: you decide with the numbers in front of you. Use `--runs 3` or more before trusting a close
call; the benchmark covers the kinds of check of its own tasks, so a lesson tested here is judged on
those, not on your real work.
