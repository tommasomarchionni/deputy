# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/). While the major version is 0, a new minor
version may contain breaking changes.

## [Unreleased]

### Added

- Repository practices: labels, milestones, a roadmap project, pull request title and regression
  checks, automatic labelling, issue triage and a stale bot.

## [0.1.0] - 2026-11-18

### Added

- MCP server with four tools (`delegate`, `status`, `profiles`, `feedback`), a confined sandbox,
  server-run checks (`contains`, `excludes`, `cites_lines`, `max_chars`, `file_written`,
  `file_contains`, `command`) and bounded self-correction.
- The `deputy` command: `init`, `doctor`, `install`, `instructions`, `bench`, `tune`, `workspace`,
  `update`, `mcp`; one-line installer; connections for Claude Code, GitHub Copilot (VS Code),
  Cursor and Codex.
- `deputy bench` and `deputy tune`, with `auto_tune` (`off`, `propose`, `apply`) and a separate
  `tuned.toml`; an estimate of the tokens saved for the orchestrator.
- `deputy update` and `auto_update` (`off`, `notify`, `apply`).
- Documentation site on GitHub Pages; CI on three Python versions and two operating systems,
  CodeQL, property tests, and releases with build provenance attestations.

[Unreleased]: https://github.com/tommasomarchionni/deputy/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tommasomarchionni/deputy/releases/tag/v0.1.0
