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

## Tuning the chosen model (`deputy bench`, `deputy tune`)

Every model behaves differently: a reasoning model needs its thinking capped, a small one needs a
shorter task. deputy measures the model **you** picked. The commands exist (see
[configuration](configuration.md#tuning)); this is what they do and why.

- `deputy bench` runs a built-in suite on a synthetic project that is identical every time: cited
  extraction, a needle in a 600-line log (paging), a file write, a secret it must refuse, a short
  summary. Graders are code. It reports pass rate, citation accuracy, turns, output tokens,
  seconds (with spread) and tokens per second, and saves a **model profile**.
- `deputy tune` runs the suite for the configured settings and for candidates (thinking off,
  shorter output, cooler sampling), then recommends one only if it loses nothing and is clearly
  cheaper than the spread.
- **Who decides** is the `auto_tune` setting: `off`, `propose` (default) or `apply`. Applied
  changes go to a separate `tuned.toml`, with the previous one kept for `deputy tune --revert`.
- **Not yet done:** re-running automatically when the model changes, and feeding the profile into
  routing. Both are planned (M4/M5); today you run `deputy tune` after changing model or server.
