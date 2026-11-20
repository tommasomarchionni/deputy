# Delegating to deputy

deputy hands reading to a cheaper model and proves the answer. Use it to save tokens, never to decide.

**Delegate** when the task needs reading more than about 4 KB (long or several files, a big log, git history) and the answer is short: summaries, inventories, cross-references, facts
with their lines, log triage, "what changed and why". Decide **before** you read the input,
and do not read it first: reading it is the cost you are avoiding.
**Do it yourself**: a lookup in one short file, anything one search answers, a file you are about to edit, decisions, anything involving secrets.

How:

1. For a question about files, call `digest` with the `files`, the `question` and an absolute
   `workdir`: deputy adds the checks. For other work, call `delegate` with a `workdir` and a task that names the files (never paste them).
   The worker has none of your context: say what to cover, in what form, and how long. Add `checks`
   that prove the answer: `cites_lines` with `path` (the cited lines exist), `contains` (facts you can predict),
   `excludes`, `max_chars`, `file_written` and `file_contains` (when it writes), `command` (allow-listed
   tests).
2. `status: ok`: do not reread the sources, but checks do not prove a source was understood: before
   acting on a fact, read its cited line.
3. `checks_failed`, `turn_limit`, `error` or `needs_frontier`: do the task yourself; do not retry
   blindly. `not_worth_delegating`: this kind of task has not saved tokens, stop delegating it. `small_input`: the files were short, read such files yourself.
4. Code changes that tests can check: `delegate` with `agent=true` before you read the code, then
   review and apply its patch. One small change per item of `steps`; tests and linters in `checks`.
   Flag `partial`: apply the patch, then do only the steps the answer names.
5. After using a result, call `feedback` with its `run_id`: `accepted` or `rejected`, and why.

The worker answers in English.
