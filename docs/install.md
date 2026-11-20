# Install and connect an agent

deputy has two parts: the `deputy` command (the MCP server) and a model server it talks to
(llama.cpp, Ollama, vLLM, LM Studio, or a hosted OpenAI-compatible API). You need both.

## 1. Install

One line, no `sudo`, everything stays in your home directory:

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
```

The script is short; [read it](https://github.com/tommasomarchionni/deputy/blob/main/install.sh) first if you like. It needs [uv](https://docs.astral.sh/uv/);
add `-s -- --install-uv` to let it install uv too (a pinned version of the official uv installer,
checked against its SHA-256 before it runs):

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh -s -- --install-uv
```

Prefer not to pipe a script? Same result, by hand:

```sh
uv tool install git+https://github.com/tommasomarchionni/deputy
```

Pin a version with `DEPUTY_REF=v0.1.0`. Remove it with `sh install.sh --uninstall`
(your config and traces are kept).

The installer picks the **latest release** (the wheel built and attested by the release workflow;
`main` if there is none yet). Update later with
`deputy update`; see [configuration](configuration.md#updates) for automatic updates.

## 2. Point it at a model

```sh
deputy init          # asks for the URL, model name and an optional key file
deputy doctor        # checks the config and that the server answers
```

Non-interactive: `deputy init --yes --url http://127.0.0.1:8080 --model default`.
Details and examples for each kind of server: [configuration](configuration.md).

For the local agent (`delegate` with `agent=true`), the server needs a context of at least 110,000
tokens: with llama.cpp, start it with `--ctx-size 131072`. The `claude` command must be installed.
Give the server room to keep a long conversation aside (llama.cpp: `--cache-ram`, in MiB): with
one slot, any other request to the same server throws the agent's conversation out of memory, and
without that room the agent reads it all again at its next turn. Measured on a 27B model with
`--cache-ram 2048`, at 47,000 tokens of conversation: 10 minutes a turn instead of seconds, while
a benchmark was running on the same server. `deputy bench` therefore does not start while an
agent run is going (exit code 75).

## 3. Connect your agent

`deputy install <agent>` prints the exact configuration; add `--apply` to do it for you.

| Agent | Command | What `--apply` does |
| --- | --- | --- |
| Claude Code | `deputy install claude-code --apply` | runs `claude mcp add --scope user deputy -- deputy mcp` |
| GitHub Copilot (VS Code) | `deputy install vscode --apply` | adds the server to `.vscode/mcp.json` of the current project |
| Cursor | `deputy install cursor --apply` | adds it to `.cursor/mcp.json` |
| Codex CLI | `deputy install codex --apply` | writes the guide in `~/.codex/AGENTS.md` and prints a TOML block to add to `~/.codex/config.toml` |
| Anything else that speaks MCP | `deputy install generic` | prints a JSON block; the command is `deputy mcp` over stdio |

Restart the agent (or reload its MCP servers) afterwards.

**The read hint (Claude Code, on by default).** `deputy install claude-code --apply` also adds a
hook to `~/.claude/settings.json`: before Claude Code reads a whole file larger than about 8 KB
(about 2,000 tokens), or runs a shell command that prints more than that (`cat`, `nl`, `sed -n`,
`head`, `tail`), it adds one line to the model's context with the size and the `digest` tool that
can read it instead. Images, PDFs and other binary files get no hint: the worker reads text only.
With a local default backend the same hook adds a few lines to each of your requests: code changes
that tests can check go to the local agent ([configuration](configuration.md#code-changes)).
It never blocks anything, says nothing for a small read, a partial read through the read tool or a
command piped into another, and stays silent on any error. In real sessions such reads were about 1.5 % of all reads but held
about 18 % of what was read. Turn it off with `deputy hook off` (on again with `deputy hook on`,
state with `deputy hook status`), or install without it: `deputy install claude-code --apply --no-hook`.

## 4. Teach the agent when to delegate

Delegation only saves tokens if the agent uses it on the right work, so deputy ships a short guide.

**Claude Code: nothing to do.** `deputy install claude-code --apply` installs the guide as a
user-level skill, `~/.claude/skills/deputy/SKILL.md`: it applies in every project, is loaded only
when a task matches, and deputy rewrites it when a new version starts, so it never falls behind.
Skip it with `--no-skill`.

**Codex: nothing to do either.** `deputy install codex --apply` writes the guide as a marked block in
`~/.codex/AGENTS.md` (your own text there is kept), and deputy refreshes that block when a new
version starts.

**Other agents (Copilot, Cursor), or a copy inside a project.** These read the guide from files in
the project, so `deputy instructions` writes it there, in the format each agent reads:

```sh
deputy instructions --for claude --apply        # a project copy of the Claude Code skill
deputy instructions --for copilot --apply       # GitHub Copilot: .github/instructions/deputy.instructions.md
deputy instructions --for cursor --apply        # Cursor rule
deputy instructions --for agents --apply        # AGENTS.md (Codex, Copilot and many others)
```

| `--for` | File written (in the current project) | Kind |
| --- | --- | --- |
| `claude` | `.claude/skills/deputy/SKILL.md` | owned by deputy; a skill costs no tokens until it is relevant |
| `claude-md` | `CLAUDE.md` | a marked block; always loaded |
| `copilot` | `.github/instructions/deputy.instructions.md` | owned; `applyTo: "**"` |
| `copilot-repo` | `.github/copilot-instructions.md` | a marked block |
| `cursor` | `.cursor/rules/deputy.mdc` | owned; attached when relevant |
| `agents` | `AGENTS.md` | a marked block |

Without `--apply` the command only prints what it would write; `--write FILE` inserts the guide as
a marked block in any file you name. Files deputy owns are rewritten each
time; in shared files only the block between the `deputy:start` and `deputy:end` markers is touched,
so your own text is safe. Prefer the skill over `claude-md`: a skill is loaded on demand, a
`CLAUDE.md` block on every turn.

**A copy inside a project does not update itself.** deputy never rewrites files in your repository
on its own, so after an update run the same command again. `deputy doctor`, run in the project, names
every copy older than the current guide and the command that refreshes it.

The guide is short on purpose (about 450 tokens). It says what to delegate and what not, how to write
`checks`, what to trust (`status: ok`) and what to do yourself, to review worker output, and to send
`feedback`.

## 5. Try it

Ask your agent: *"Use deputy to summarise README.md in five bullets, citing lines."* The result
carries `status`, the answer and a one-line summary of the checks that ran; metrics and the saving
estimate are in the trace (`deputy stats`).

## Troubleshooting

- **`deputy doctor` says the key was refused:** the model server wants an API key; set
  `api_key_file` (mode 600) or `api_key_env` in the config.
- **`command not found: deputy`:** run `uv tool update-shell` and open a new terminal.
- **The agent does not list the tools:** restart it; run `deputy mcp` by hand and it should wait
  silently on stdin (stop it with Ctrl-D).
- **Slow answers:** see [local serving](local-serving.md); reasoning models spend most of the time
  thinking, and capping that is the biggest lever.
