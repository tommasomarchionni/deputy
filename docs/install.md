# Install and connect an agent

deputy has two parts: the `deputy` command (the MCP server) and a model server it talks to
(llama.cpp, Ollama, vLLM, LM Studio, or a hosted OpenAI-compatible API). You need both.

## 1. Install

One line, no `sudo`, everything stays in your home directory:

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
```

The script is short; [read it](../install.sh) first if you like. It needs [uv](https://docs.astral.sh/uv/);
add `-s -- --install-uv` to let it install uv too:

```sh
curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh -s -- --install-uv
```

Prefer not to pipe a script? Same result, by hand:

```sh
uv tool install git+https://github.com/tommasomarchionni/deputy
```

Pin a version with `DEPUTY_REF=v0.1.0`. Remove it with `sh install.sh --uninstall`
(your config and traces are kept).

The installer picks the **latest release** (or `main` if there is none yet). Update later with
`deputy update`; see [configuration](configuration.md#updates) for automatic updates.

## 2. Point it at a model

```sh
deputy init          # asks for the URL, model name and an optional key file
deputy doctor        # checks the config and that the server answers
```

Non-interactive: `deputy init --yes --url http://127.0.0.1:8080 --model default`.
Details and examples for each kind of server: [configuration](configuration.md).

## 3. Connect your agent

`deputy install <agent>` prints the exact configuration; add `--apply` to do it for you.

| Agent | Command | What `--apply` does |
| --- | --- | --- |
| Claude Code | `deputy install claude-code --apply` | runs `claude mcp add --scope user deputy -- deputy mcp` |
| GitHub Copilot (VS Code) | `deputy install vscode --apply` | adds the server to `.vscode/mcp.json` of the current project |
| Cursor | `deputy install cursor --apply` | adds it to `.cursor/mcp.json` |
| Codex CLI | `deputy install codex` | prints a TOML block for `~/.codex/config.toml` |
| Anything else that speaks MCP | `deputy install generic` | prints a JSON block; the command is `deputy mcp` over stdio |

Restart the agent (or reload its MCP servers) afterwards.

## 4. Teach the agent when to delegate

Delegation only saves tokens if the orchestrator uses it on the right work. One command adds a
short guide to the file your agent already reads:

```sh
deputy instructions --write CLAUDE.md                      # Claude Code
deputy instructions --write .github/copilot-instructions.md   # GitHub Copilot
deputy instructions --write AGENTS.md                      # Codex and others
```

It is idempotent (re-running refreshes the block between its markers) and about 200 tokens.

## 5. Try it

Ask your agent: *"Use deputy to summarise README.md in five bullets, citing lines."* The result
carries `status`, the checks that ran, metrics, and `orchestrator_tokens_saved_est`.

## Troubleshooting

- **`deputy doctor` says the key was refused:** the model server wants an API key; set
  `api_key_file` (mode 600) or `api_key_env` in the config.
- **`command not found: deputy`:** run `uv tool update-shell` and open a new terminal.
- **The agent does not list the tools:** restart it; run `deputy mcp` by hand and it should wait
  silently on stdin (stop it with Ctrl-D).
- **Slow answers:** see [local serving](local-serving.md); reasoning models spend most of the time
  thinking, and capping that is the biggest lever.
