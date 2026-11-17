# Configuration

`deputy init` writes `~/.config/deputy/config.toml` (mode 600). Change the folder with
`DEPUTY_HOME`, or point at a file with `DEPUTY_CONFIG`. Traces go to `~/.local/state/deputy`
(`DEPUTY_STATE`). **Secrets never go in the file:** use `api_key_file` (must be mode 600) or
`api_key_env`.

```toml
default_backend = "local"

[[backends]]
name = "local"
base_url = "http://127.0.0.1:8080"   # the server root; deputy calls /v1/chat/completions
model = "default"                    # sent in each request; llama.cpp ignores it
kind = "local"                       # local | hosted
api_key_file = "~/.config/deputy/key"
timeout_s = 600
max_tokens = 4096
extra_body = { temperature = 0.2 }   # merged into every request

[policy]
# Commands a `command` check may run (argv prefixes, no shell). Empty = none.
allowed_commands = [["node", "--test"], ["python", "-m", "pytest"]]
command_timeout_s = 120
```

## Backends

Anything that serves the OpenAI chat API with tool calling.

| Server | `base_url` | Notes |
| --- | --- | --- |
| llama.cpp `llama-server` | `http://127.0.0.1:8080` | start it with `--jinja` so tool calls work |
| Ollama | `http://127.0.0.1:11434` | `model` = the Ollama model name |
| LM Studio | `http://127.0.0.1:1234` | enable the local server |
| vLLM | `http://127.0.0.1:8000` | `--enable-auto-tool-choice` |
| Hosted (OpenAI-compatible) | the provider's root | `kind = "hosted"`, key via `api_key_env`; your files leave the machine |

Several backends can be listed; pick one per call with the `backend` argument.

## Profiles

A profile is a markdown file with a short front matter and the instructions the worker follows.
Bundled: `helper`. Add your own in `~/.config/deputy/profiles/` (yours) or `.deputy/profiles/`
(per project; the project wins). See `examples/project-dev.md` for a template.

## Policy

The orchestrator may declare `command` checks, but they only run if the argv starts with an entry
of `allowed_commands`. The worker model never runs anything.
