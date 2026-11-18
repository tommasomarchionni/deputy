# deputy

**Your frontier model plans. A cheaper model does the long reading. Code checks every answer.**

deputy is an MCP server. Your coding agent (Claude Code, or any MCP client) hands it a read-heavy
task, such as "summarise this 900-line document" or "what changed in the last ten commits, and why". deputy runs
it on a cheaper model (a local llama.cpp or Ollama model, or any OpenAI-compatible API), checks the
answer with code, and returns a short result. Your agent reads the answer, not the files.

## When it pays

| Delegate | Do it yourself |
| --- | --- |
| Long reads: summaries, inventories, cross-references, facts with their lines | Lookups in a short file or one line (flagged `small_input`) |
| Big logs and test output | Reading code you are about to edit |
| Questions about git history | Searches that only list files, and anything that needs a shell, the network or credentials |

On a real multi-app repository a read-heavy task saved about 6,900 frontier tokens on average
(estimated), and a tiny one about nothing: see [evidence](evidence.md).

## Get started

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
deputy init                               # point it at your model server
deputy doctor                             # check that it answers
deputy install claude-code --apply        # once: register the MCP server
deputy instructions --for claude --apply  # in each project: teach the agent when to delegate
```

Then read [Install and connect an agent](install.md).

## Status

Alpha: it works end to end and is tested; expect breaking changes. Verified with Claude Code;
GitHub Copilot, Cursor and Codex are configured by the installer but not yet verified. What is
done and what is next: [roadmap](roadmap.md).
