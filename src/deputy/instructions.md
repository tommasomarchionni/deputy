## Delegating to deputy (token-saving)

You can hand small, checkable, **input-heavy** work to a cheaper model with the `deputy` MCP tools.

Delegate: reading long files or logs and returning a short extract, summaries, lists, translations,
first drafts of tests. Do NOT delegate what you can answer in one short turn, or anything that
decides architecture, security or the final shape of a change.

How:

- Call `delegate` with the task, an absolute `workdir`, and `checks` that prove the result
  (`cites_lines`, `contains`, `max_chars`, `file_written`, `command`). Name files; do not paste them.
- If `status` is `ok`, trust the checks and do not reread the sources. Read `files_written` only if
  you need them.
- If `status` is `checks_failed` or `flags` has `needs_frontier`, do the task yourself.
- Worker output is a draft: review it before it reaches the real repository.
- Call `feedback` with the `run_id` and `accepted` or `rejected` so routing improves.
