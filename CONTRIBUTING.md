# Contributing

Thanks for helping. deputy is early: the most useful contributions today are **ideas with
evidence** (a failing case, a measurement) and **review of the design** in `docs/`.

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

Releases are tags, and the version lives in `pyproject.toml`.

1. Open a pull request "chore: release vX.Y.Z" that runs `uv version X.Y.Z` (this also updates
   `uv.lock`), and merge it once CI is green.
2. Tag the merge commit and push the tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
3. The `Release` workflow checks that the tag matches the version, runs the tests, builds the wheel
   and the sdist, attests their provenance, and publishes a GitHub release with generated notes.
   `deputy update` and the installer pick the new release up.
4. PyPI publishing is off until you set the repository variable `PYPI_PUBLISH=true` and configure
   trusted publishing for the `pypi` environment on pypi.org.

Versioning: while the major is 0, a new minor may break things; patches never should.

## Pull requests

1. Fork, branch from `main`, make the change.
2. Run the checks in `.github/workflows/ci.yml` locally where you can.
3. Open the pull request; fill in the template. CI must be green; the maintainer reviews.
4. Squash merge only. History stays linear.

## Contributions and license

By contributing you agree your work is licensed under Apache-2.0, like the project, and that you
follow the [Code of Conduct](CODE_OF_CONDUCT.md). Sign off commits (`git commit -s`, DCO).
