# deputy

**Your frontier model plans and reviews. A cheaper model does the long reading and the code changes
that tests can check. Code checks every result.**

deputy is an MCP server that lets your coding agent (Claude Code, or any MCP client) keep its
tokens for planning and review. It takes two kinds of work off the frontier model:

- **Code changes that tests or linters can check.** A local agent makes them on your own model
  (llama.cpp), in a private copy of the repository, one change per short conversation, until the
  project's checks pass. Your agent gets a patch to review and apply:
  [how](tools.md#a-local-agent-for-a-code-change-agenttrue).
- **Long reading.** "Summarise this 900-line document", "what changed in the last ten commits, and
  why": a cheaper model (local, or any OpenAI-compatible API) reads, code proves every quoted line,
  and your agent reads a short answer, not the files: [how](tools.md#digest).

## When it pays

| Hand over | Do it yourself |
| --- | --- |
| Code changes you can state and check: a list of small changes with their tests, removals, renames, mechanical edits across files | A change of a few lines you are waiting for: a local model takes minutes where a hosted one takes seconds |
| Long reads: summaries, inventories, cross-references, facts with their lines | A change that needs analysis nobody has done yet |
| Big logs and test output | Lookups in a short file or one line (flagged `small_input`), and code you are about to edit |
| Questions about git history | Searches that only list files |

What was measured, with its limits: [evidence](evidence.md).

- Eight small changes to this repository handed to a local 27B model in eight steps: about 43 %
  fewer frontier tokens than the frontier model alone, with the same gates passing, and 88 minutes
  of waiting where the frontier model took 2.5.
- On 21 everyday reading tasks on two real repositories, run twice, a reading task saved about
  4,800 to 5,600 frontier tokens (estimated) and every answer was correct; a lookup saved about
  nothing.
- On one real project the large reads were at most 7 % of what the sessions spent, the subagents
  that edit code 43 %, and reading again a context above 200,000 tokens 38 %.
  [`deputy discover`](self-improvement.md) prints the same table for your own sessions.

## Get started

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
deputy init                               # point it at your model server
deputy doctor                             # check that it answers, and what the local agent can use
deputy install claude-code --apply        # once: the MCP server, the skill and the hints
```

Then read [Install and connect an agent](install.md).

## Status

Alpha, in daily use: it works end to end, is tested and measured on real repositories, and is used
to develop a real multi-app project; expect breaking changes. Verified with Claude Code;
GitHub Copilot, Cursor and Codex are configured by the installer but not yet verified. The local
agent needs Claude Code. What is done and what is next: [roadmap](roadmap.md).
