# Roadmap

Each milestone says what must be **proven**, not only built. The plan lives in the
[milestones](https://github.com/tommasomarchionni/deputy/milestones) and the
[project board](https://github.com/users/tommasomarchionni/projects/3).

| Milestone | Proven by | State |
| --- | --- | --- |
| **M1 Core server** | the server, sandbox, checks and installer work end to end with a real model | done (v0.1.0) |
| **M2 Benchmark and tuning** | reproducible numbers with spread for the models in use; `deputy tune` applies a setting only when it wins, with rollback | in progress: `bench` and `tune` exist; more tasks, repeats and a held-out split are open |
| **M3 Verification and correction** | citation accuracy and fact coverage rise on the benchmark; files larger than the model context are handled | planned |
| **M4 Profiles and memory** | a run with memory beats the same run without it (ablation); profiles are measured, not typed | planned |
| **M5 Async and routing** | slow models do not block the agent; the router picks the cheapest passing path | done: background tasks (`wait=false`, `collect`) and the router |
| **M6 Second agent** | the same server driven from GitHub Copilot (VS Code) gives the same benchmark results as through Claude Code | planned; instruction files for every agent exist, the real-app verification does not |
| **M7 Improvement loop** | an offline optimiser proposes profile and parameter changes with a report; a human approves; rollback works | planned |
