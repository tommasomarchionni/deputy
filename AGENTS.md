# AGENTS.md

Guidance for AI coding agents working on this repository.

- Read `README.md`, `docs/architecture.md` and `CONTRIBUTING.md` before changing anything.
- The project is in design: change documents and tooling only unless an issue says otherwise.
- Never weaken the sandbox, the check allow-list or secret handling.
- Do not add dependencies, workflows or permissions without saying why in the pull request.
- Pin every GitHub Action to a full commit SHA; keep workflow `permissions` minimal.
- Conventional Commit titles; one change per pull request; no secrets in the repository.
- Report what was verified and what was not. Do not invent results.
- Work starts from an issue (`status: ready`); one issue, one pull request; branch
  `<type>/<N>-<slug>`; title `type(area): subject`; body `Closes #N` and a `Regression:` line for
  `feat` and `fix`. See `CONTRIBUTING.md` ("How the project is run").
- Never merge your own pull request without a green `Required` and `PR title`; never disable a
  required check to make something pass.
