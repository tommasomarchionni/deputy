# Memory

!!! note "Status: mostly built"
    Built: the measured model profile (written by `deputy bench`, read back for the turn limit and by
    `status`), the trace of every run and `feedback` (reported by `deputy stats`), and **lessons**
    derived from the trace (`deputy learn`, see
    [configuration](configuration.md#lessons-learned-from-use)). Lessons are tested
    against the benchmark (`deputy lessons check`). **Project memory** (pointers and a
    glossary, kept per repository) is built too, see
    [configuration](configuration.md#project-memory). The rest of this page is the design.

Does memory make sense? Yes, but narrowly. A model that is not trained on your project forgets
everything between calls; the useful part of memory is what is **measured and verified**, not a
diary. Three layers, each with a different owner and lifetime.

## 1. Model profiles (shared, machine-wide)

Measured facts about a model *and its serving setup*: tokens/s, the context that really works,
tool-call reliability, best reasoning budget per task class, failure habits (for example: cites
wider line ranges, asks for a shell). Written by the benchmark, never by a model. Highest value,
lowest risk.

## 2. Lessons (shared and per project)

A lesson is a correction that was **validated**: "rule X added to profile Y raised line-citation
accuracy from 81% to 96%". Fields: scope (model, task class, project), the rule text, evidence
(run ids), confidence, expiry. Retrieved only when the task class matches, under a small token
budget. Lessons that stop paying are removed.

## 3. Project memory (per repository)

Pointers first: where the rules live (`.github/instructions/`, `AGENTS.md`), the commands that
verify, the glossary, past accepted diffs as examples. **Point to the source of truth instead of
copying it**, so memory cannot go stale or contradict the repository. Stored in the user data
directory keyed by repository id, exportable to reviewed markdown. Built: `deputy memory`.

## Rules

- Workers cannot write memory. Model profiles come from the benchmark, lessons from the trace
  (`deputy learn`), project memory from you (`deputy memory`); all are JSON files in the state
  directory, and project memory can be exported as markdown to review.
- Lessons have evidence and an expiry, and their text comes from a fixed catalogue: nothing from a
  trace or a worker is copied into a prompt. No secrets, tokens or personal data.
- A repository's memory is never added to another repository's task.
- **Ablation:** a lesson stays only while a benchmark run with it is not worse than without it
  (`deputy lessons check`). Project memory has no ablation yet.
- Selection is simple: lessons by profile and kind of check, project memory by repository, both
  under a small size budget. No search index; embeddings only if they ever win on the benchmark.
