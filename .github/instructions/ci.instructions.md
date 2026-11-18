---
description: Workflows, release and repository automation
applyTo: '.github/workflows/**,tools/ci/**,.github/labels.json'
paths: '.github/workflows/**,tools/ci/**,.github/labels.json'
---

# Workflows and automation

- Pin every action to a full commit SHA with the version in a comment; read-only `permissions` by
  default, and write permissions only on the job that needs them. No `pull_request_target`.
- Scripts in `tools/ci/` are code: typed, tested in `tests/ci/`, and they fail loudly (a step that
  pipes a script through `tee` needs `set -o pipefail`).
- `ubuntu-latest` runners; a job must not assume an absolute host path.
- A rule changes with a pull request that changes it where it is written (CONTRIBUTING.md,
  AGENTS.md, the instructions) and where it is enforced, with its test.
- Releases are automatic: `tools/ci/release_plan.py` decides, `release.yml` does it in one run. Do
  not add a version commit; the version comes from the git tag.
- Labels live in `.github/labels.json` and are synced by a workflow; never create them by hand.
