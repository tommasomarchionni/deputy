# Contributing

Thanks for helping. deputy is early: the most useful contributions today are **ideas with
evidence** (a failing case, a measurement) and **review of the design** in `docs/`.

## How the project is run

- **Issue first.** Work starts from an issue, from a form (bug, idea or task). A direct request
  gets an issue too, written before the code. Urgent fixes to a broken `main` get theirs right after.
- **Labels** (`.github/labels.json`, applied by a workflow): `type:` (feature, bug, task, docs,
  security, decision, verification), `area:` (server, sandbox, checks, bench, cli, installer, memory,
  router, docs, ci), `priority:` (p0 drop everything, p1 next, p2 planned, p3 nice to have) and
  `status:` (needs-triage, ready, blocked, needs-decision). Forms set type, area and priority; a new
  issue starts as `status: needs-triage`. Work only on `status: ready` issues.
- **Milestones** M1 to M7 match the [roadmap](docs/roadmap.md); each has an exit criterion in its
  description. The current milestone is the first one with open issues; inside it, p0 first, then
  p1, p2, p3, lowest number first. Moving an issue or changing its priority needs a comment with
  the reason.
- **Project.** The [Roadmap board](https://github.com/users/tommasomarchionni/projects/3) groups the
  issues; the project's own "auto-add" workflow puts new issues on it.
- **Pull requests.** One issue, one pull request; branch `<type>/<N>-<slug>`; the title is a
  Conventional Commit with the area as scope (`fix(checks): ...`) and the body says `Closes #N`
  (or `Refs #N` if partial). `feat`, `fix` and `perf` must change the product (`src/deputy/` or
  `install.sh`); tests, docs, CI and tooling use `test`, `docs`, `ci`, `chore` or `build`.
  A `feat` or `fix` also carries a test, or the line `Regression: none - <reason>` (at least 20
  characters). Automation adds the area labels, copies type, priority and milestone from the issue
  and assigns the author; the `PR title` check enforces the rest.
- **Evidence in the pull request**, not in files: what was run, what passed, what was not checked.
  Acceptance criteria of the issue are ticked one by one with their proof.
- **After the merge** the issue closes with `Closes #N`; check that its criteria are ticked and that
  the milestone moved. A red `main` is a `priority: p0` issue and comes first.
- **A rule changes with a pull request** that changes it here, in the same delivery as the code or
  workflow that enforces it (`tools/ci/` has tests).

## Ground rules

- **Measure before you change.** A change to behaviour, prompts or parameters comes with benchmark
  numbers (see `docs/self-improvement.md`). "It feels better" is not a reason.
- **Open an issue first** for anything bigger than a typo, so the direction is agreed before you
  spend time.
- **One change per pull request**, small, with a clear title in Conventional Commits form
  (`feat: ...`, `fix: ...`, `docs: ...`, `ci: ...`, `chore: ...`).
- **Tests are part of the change.** A bug has a test that fails before and passes after.
- **Security first.** Never weaken the sandbox, the check allow-list or secret handling to make
  something pass.
- No secrets, tokens, private code or personal data in issues, tests or the benchmark.

## Development setup

```sh
git clone https://github.com/tommasomarchionni/deputy && cd deputy
uv sync                                   # creates .venv with the dev tools
uv run ruff check . && uv run ruff format --check .
uv run pyright                            # strict on src/
uv run pytest --cov                       # unit and property tests; coverage gate 85%
uv run mkdocs serve                       # the documentation site, live
```

Optional: `uvx pre-commit install` runs ruff and basic hygiene checks before each commit.

Tests against a real model are skipped by default; to run them:
`DEPUTY_LIVE_URL=http://127.0.0.1:8080 uv run pytest tests/live`.

Style: small functions with one job, intention-revealing names, no flag arguments that switch
behaviour, polymorphism instead of long `if` chains, no comments that restate the code. Everything
(code, comments, documentation, messages, tests) is written in English.

## Releasing

Releases are automatic and can be forced by hand. The version comes from the git tag, so there is
no version commit and no release pull request.

- **When:** after CI succeeds on `main`, a release is cut if 10 releasable changes (`feat`, `fix`,
  `perf`; or any breaking change) have piled up since the last tag. On the 12-hour schedule it is
  also cut if at least 12 hours have passed since the last release and there is something to release.
- **Which version:** while the major is 0, a feature or a breaking change bumps the minor and
  anything else the patch; from 1.0, a breaking change bumps the major.
- **By hand:** Actions, "Release", "Run workflow". `force` skips the thresholds. Nothing skips the
  safety checks: `main` must be green and there must be at least one releasable change.
- **What it does:** tags the commit, runs the tests, builds the wheel and the sdist, attests their
  provenance, and publishes a GitHub release with generated notes. `install.sh` and `deputy update`
  install that wheel.
- **PyPI** stays off until you set the repository variable `PYPI_PUBLISH=true` and configure trusted
  publishing for the `pypi` environment on pypi.org.

The rules are in `tools/ci/release_plan.py` and are tested (`tests/ci/test_release_plan.py`); a
change to the cadence is a pull request that changes them there and here.

## Pull requests

1. Fork, branch from `main`, make the change.
2. Run the checks in `.github/workflows/ci.yml` locally where you can.
3. Open the pull request; fill in the template. CI must be green; the maintainer reviews.
4. Squash merge only. History stays linear.

## Contributions and license

By contributing you agree your work is licensed under Apache-2.0, like the project, and that you
follow the [Code of Conduct](CODE_OF_CONDUCT.md). Sign off commits (`git commit -s`, DCO).
