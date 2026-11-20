# Roadmap

Each milestone says what must be **proven**, not only built. The plan lives in the
[milestones](https://github.com/tommasomarchionni/deputy/milestones) and the
[project board](https://github.com/users/tommasomarchionni/projects/3).

| Milestone | Proven by | State |
| --- | --- | --- |
| **M1 Core server** | the server, sandbox, checks and installer work end to end with a real model | done (v0.1.0) |
| **M2 Benchmark and tuning** | reproducible numbers with spread for the models in use; `deputy tune` applies a setting only when it wins, with rollback | in progress: `bench` (built-in suite of 12 fast tasks, your own task file, comparison of two runs), `tune`, `stats` and `discover` work; the everyday-use bar ([#115](https://github.com/tommasomarchionni/deputy/issues/115)) is met: 21 real tasks, two runs, every answer correct |
| **M3 Verification and correction** | citation accuracy and fact coverage rise on the benchmark; files larger than the model context are handled | in progress: `cites_text`, `cites_lines` with `path`, whole-value `contains`, `scan_file`, `summarise_log`, `list_files`, read-only `git` and grouped search exist; an outline tool was measured and not needed |
| **M4 Profiles and memory** | a run with memory beats the same run without it (ablation); profiles are measured, not typed | in progress: project memory and lessons exist; a lesson is kept only if the benchmark with and without it shows it does not hurt |
| **M5 Async and routing** | slow models do not block the agent; the router picks the cheapest passing path | done: background tasks (`wait=false`, `collect`) and the router; publishing to PyPI for `uvx deputy mcp` ([#26](https://github.com/tommasomarchionni/deputy/issues/26)) awaits a decision |
| **M6 Second agent** | the same server driven from GitHub Copilot (VS Code) gives the same benchmark results as through Claude Code | in progress: instruction files for every agent exist and Claude Code has a read hint (`deputy hook`); the real-app verification with Copilot, Cursor and Codex does not |
| **M7 Improvement loop** | settings and lessons change only when a measurement says they win, with a human gate and rollback | `bench`, `tune` (approval and rollback) and `learn` (lessons kept only if they help) |
