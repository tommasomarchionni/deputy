# Roadmap

Each milestone says what must be **proven**, not only built.

- **M0 — repository and design** (now). Documents, imported agent profiles, sandbox script.
- **M1 — core.** MCP server skeleton, adapter interface, llama.cpp and OpenAI/Anthropic-compatible
  adapters, sandbox, structured results. *Proven by:* contract tests per adapter; sandbox escape tests.
- **M2 — benchmark.** Task set, deterministic graders, runner, baseline for the models on hand.
  *Proven by:* reproducible numbers with spread.
- **M3 — verification and correction.** Declared checks run by the server, bounded retries,
  chunking of long files. *Proven by:* citation accuracy and coverage up on the benchmark.
- **M4 — profiles and memory.** Measured model profiles, lessons, project memory with ablation.
  *Proven by:* a run with memory beats one without.
- **M5 — async and routing.** MCP tasks for slow models, routing by measured profile.
- **M6 — second agent.** Same server driven from GitHub Copilot; same benchmark, same results.
- **M7 — improvement loop.** Offline optimiser proposing changes with reports; human gate.
