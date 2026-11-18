# Memory

!!! note "Status: design"
    Only two pieces exist today: `deputy bench` writes a measured model profile, and every run and
    `feedback` is recorded in a trace that `deputy stats` reports on. Nothing learns from them yet. Loading profiles, lessons and project
    memory are planned ([#13](https://github.com/tommasomarchionni/deputy/issues/13),
    [#14](https://github.com/tommasomarchionni/deputy/issues/14),
    [#15](https://github.com/tommasomarchionni/deputy/issues/15)). The rest of this page is the design.

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
directory keyed by repository id, exportable to reviewed markdown.

## Rules

- Workers cannot write memory. Entries come from the benchmark or from `deputy_feedback`, and a
  human can review them (markdown export, diffable).
- Every entry has provenance and an expiry. No secrets, tokens or personal data.
- A repository's memory is never injected into another repository's task.
- **Ablation:** memory stays only while a run with it beats a run without it on the benchmark.
- Retrieval: BM25 (SQLite FTS5) first; embeddings only if they win on the benchmark.
