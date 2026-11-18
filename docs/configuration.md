# Configuration

`deputy init` writes `~/.config/deputy/config.toml` (mode 600). Change the folder with
`DEPUTY_HOME`, or point at a file with `DEPUTY_CONFIG`. Traces go to `~/.local/state/deputy`
(`DEPUTY_STATE`). **Secrets never go in the file:** use `api_key_file` (must be mode 600) or
`api_key_env`.

```toml
default_backend = "local"
auto_tune = "propose"                # off | propose | apply, see "Tuning" below
auto_update = "notify"               # off | notify | apply, see "Updates" below

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

A profile is a markdown file with a short front matter and the instructions the worker follows.
Bundled: `helper`. Add your own in `~/.config/deputy/profiles/` (yours) or `.deputy/profiles/`
(per project; the project wins). See
[`examples/project-dev.md`](https://github.com/tommasomarchionni/deputy/blob/main/examples/project-dev.md)
for a template.

The front matter can set the profile's limits:

```yaml
---
name: strict-reader
description: Reads and reports, no second chances
max_turns: 4        # 1 to 50: the most tool turns the worker may take
max_retries: 0      # 0 to 5: correction rounds after a failed check (default 1)
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

## Tuning

`deputy bench` measures the model you configured; `deputy tune` also tries a few request-level
settings and recommends the best one that loses no accuracy.

```sh
deputy bench                 # one pass of the built-in suite, prints a table, saves a model profile
deputy bench --runs 3        # repeat: you get the spread too, and a fairer comparison
deputy bench --json          # the same, as JSON for scripts and reports
deputy bench --run-code      # also tasks whose grader runs model-written code (temp dir, 30 s)
deputy tune                  # baseline + candidates; recommends, or applies per `auto_tune`
deputy tune --apply          # write the winner to tuned.toml now
deputy tune --revert         # undo the last applied tuning
```

`auto_tune` decides what `deputy tune` may do alone: `off` changes nothing, `propose` (default)
prints the recommendation, `apply` writes it. Applied settings go to `tuned.toml` next to your
config, never into your config; they overlay the backend's `extra_body` and `max_tokens`, and
deleting the file undoes them. A candidate is chosen only if it keeps pass rate and citation
accuracy and is faster than the measured spread or uses at least 15% fewer output tokens.

Server flags (context size, thinking budget, cache) are outside deputy's reach: when thinking
dominates the time, `tune` prints the flag to try instead of changing anything. Custom candidates:
`deputy bench --variant 'name={"extra_body":{"temperature":0.1},"max_tokens":512}'`.

## Language

Everything deputy ships (code, documentation, messages and the bundled profile) is in English, and
the bundled `helper` profile makes the worker answer in English. A custom profile can say otherwise.

## Updates

deputy never phones home with your data. The only request it makes is a GET of the public "latest
release" of this repository, at most once a day (cached; one hour after a failure).

- `auto_update = "notify"` (default): `deputy doctor` and the `status` tool say when a newer release
  exists. Nothing is installed.
- `auto_update = "off"`: no request at all.
- `auto_update = "apply"`: at server start, a newer **compatible** release is installed (same major
  version; while the major is 0, the same minor too, because a new 0.x minor may break things).
  A new major version is never installed without you. The new version runs from the next start.

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
then `gh attestation verify <file>.whl -R tommasomarchionni/deputy`.

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
use (delete `runs.jsonl` for a clean start).

### How the saving is estimated

For each delegation deputy estimates two things with the same yardstick: what the worker read on the
orchestrator's behalf (`read`: the lines it read, plus every file in which a search found a match),
and what the orchestrator paid (`cost`: the request it wrote, serialised, plus the result it reads,
serialised). The saving is `read - cost`, and **it can be negative**: a task that reads little and
returns a lot costs more than it spares, and `not_worth_delegating` ([tools](tools.md)) is built on
that. The trace keeps all three numbers; `deputy stats` shows the per-run `read` and `cost`.

The yardstick is the size in UTF-8 bytes divided by four, rounded up. It is the usual rule of thumb
for English prose, and it is wrong by up to about a quarter either way: code and JSON are denser than
this says, other languages vary. Because both sides use it, the difference is meaningful as an order
of magnitude and a trend, not as an invoice. Not counted: the tool schemas the orchestrator carries
on every turn (about 1,200 tokens, a fixed cost with or without delegation), and its own reasoning.

## The measured model profile

`deputy bench` and `deputy tune` save what they measured for your model (pass rate, mean turns,
seconds, and the model name the server answered with) in `~/.local/state/deputy/models/`. deputy
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
