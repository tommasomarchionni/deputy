# Self-improvement

"Improves continuously" is only real if improvement is **measured**. Four levels, each with a gate.

## L0, inside a run: self-correction

Verify with declared checks, retry with the exact failure, escalate to the orchestrator when it
does not converge. Bounded retries, bounded depth, bounded budget.

## L1, between runs: learning from feedback

The orchestrator reports accepted / rejected with a reason. Recurrent rejections become candidate
lessons (see `memory.md`). A candidate enters memory only if the benchmark confirms it.

## L2, offline: optimise prompts, profiles, parameters

Reflective prompt search (for example GEPA-style evolution or DSPy-style optimisers) proposes
variants of agent profiles and parameters. Each candidate runs on the benchmark and is promoted
only if it wins.

## L3, the system itself: proposals, not edits

Changes to server code are written as a branch with the benchmark report attached. A human merges.
Nothing rewrites its own code or prompts unattended in production.

## The benchmark

- Tasks from real work: cited extraction, required-facts coverage, code that must pass a test,
  translation with terms and numbers preserved, a file larger than the context, a task that must
  be refused (write outside the sandbox, ask for a shell).
- **Deterministic graders** (code, not a model). A model judge only for style, and never alone.
- N repetitions per task (temperature is not zero), report mean and spread.
- A held-out set never used to tune, to avoid fitting the benchmark.
- Metrics: `tokens_saved` for the orchestrator (the headline number), pass rate, citation accuracy, fact coverage, turns, tokens, wall-clock, escalations.
- Mutation checks for generated tests: break the code, the test must fail.
- A frontier baseline on synthetic tasks only, never with private code, and only when allowed.

## Guardrails

Human gate on promotion; shadow mode before a change goes live; one-command rollback; a kill switch;
an audit trail; limits on recursion and budget; no private data in the benchmark.
