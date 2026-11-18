---
description: Writing documentation and the README
applyTo: 'docs/**,README.md,CONTRIBUTING.md,AGENTS.md,CLAUDE.md'
paths: 'docs/**,README.md,CONTRIBUTING.md,AGENTS.md,CLAUDE.md'
---

# Documentation

- English, plain and direct. Short sentences; say what to do, then why.
- **Say what works today.** Planned work is marked as planned and links its issue; never describe a
  roadmap item as if it existed. If you change behaviour, change the docs in the same pull request.
- Every command shown must run as written. Prefer a table for options, a short code block for usage.
- The site builds with `mkdocs build --strict`: no broken links, every page in `mkdocs.yml`. Links to
  files outside `docs/` use the GitHub URL.
- Keep the README a front door (what, why, quick start, where to read more); details live in `docs/`.
- Markdown passes `markdownlint` (see `.markdownlint-cli2.yaml`).
