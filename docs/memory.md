# Memory

!!! note "Status: mostly built"
    Built: the measured model profile (written by `deputy bench`, read back for the turn limit and by
    `status`), and the trace of every run and `feedback` (reported by `deputy stats`).

Lessons and project memory are removed: they were not used.

What is kept is what is **measured and verified**, not a diary:

## Model profiles (shared, machine-wide)

Measured facts about a model *and its serving setup*: tokens/s, the context that really works,
tool-call reliability, best reasoning budget per task class, failure habits (for example: cites
wider line ranges, asks for a shell). Written by the benchmark, never by a model. Highest value,
lowest risk.

## Rules

- No secrets, tokens or personal data.
- A repository's profile is never added to another repository's task.
