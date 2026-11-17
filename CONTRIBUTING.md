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

## Pull requests

1. Fork, branch from `main`, make the change.
2. Run the checks in `.github/workflows/ci.yml` locally where you can.
3. Open the pull request; fill in the template. CI must be green; the maintainer reviews.
4. Squash merge only. History stays linear.

## Contributions and license

By contributing you agree your work is licensed under Apache-2.0, like the project, and that you
follow the [Code of Conduct](CODE_OF_CONDUCT.md). Sign off commits (`git commit -s`, DCO).
