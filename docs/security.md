# Security model

- **The worker is untrusted.** It can only call `read_file`, `write_file`, `grep`, `list_files`, `summarise_log`,
  `scan_file` and a read-only `git`, all confined
  to `workdir` with `realpath` checks: `..`, absolute paths and symlinks pointing outside are
  refused. Files that look like secrets (`.env*`, `*.pem`, `*.key`, SSH keys, `.npmrc`, `.netrc`)
  are never readable or writable.
- **Read-only git.** The worker's `git` runs only `log`, `show`, `diff` and `blame`, with an argv
  built from an allow-list of options (no shell). Refused: any other subcommand, any option off the
  list (`--output`, `--ext-diff`, `-c`, `--exec`...), `rev:path` blob reads and paths outside the
  folder. Git runs with system and global config ignored, and with external diff, textconv, pager,
  fsmonitor and hooks off. Secret-looking files are excluded from every command by pathspec, so
  their content never appears, not even from history.
- **The worker has no shell.** Checks that run commands are declared by the orchestrator and run by
  the server, only when the argv matches `policy.allowed_commands`, without a shell, with a stripped
  environment and a timeout.
- **The orchestrator is trusted** to choose `workdir` and checks: it is the user's own agent.
- **Worker output is a draft.** Review it before it reaches the real repository.
- **Secrets:** keys come from a mode-600 file or an environment variable, are never written to the
  config, the traces or the model prompt.
- **Hosted backends send your files to that provider.** Mark them `kind = "hosted"` and use them
  only for data you may share.
- **The read hint** (Claude Code, on by default) is a hook in `~/.claude/settings.json` that runs
  `deputy-hook` before each read: it only looks at the size of the file Claude Code is about to read,
  never its content, never blocks the read, has a 5-second timeout, and adds at most one line to the
  model's context.
  `deputy hook off` removes it; other hooks and settings are kept.
- **Traces** (`runs.jsonl`) hold run metadata and check results, not file contents or answers.

## The local agent

`delegate` with `agent=true` gives a local model Claude Code's tools, including a shell. That is a
different trust level from the worker's file tools, so it is confined on every side:

- **A private copy.** The agent works in a git worktree of the repository, never in the repository
  itself. You get a patch to review before anything changes.
- **Its file tools stay in the copy.** Read, Edit and Write are allowed only inside the copy; any
  other path is refused without asking (`dontAsk` mode).
- **Its shell is sandboxed** by Claude Code's sandbox (Seatbelt on macOS, bubblewrap on Linux),
  which fails closed: no network at all, no reading of `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.kube`,
  `~/.docker`, `~/.config/deputy`, `~/.config/gh`, `~/.claude` or `.env` and key files, and writes
  only inside the copy. Commands can never run outside the sandbox.
- **No credentials.** The agent's environment drops `ANTHROPIC_*`, `CLAUDE_CODE_*`, `AWS_*`,
  `GITHUB_*` and `GH_*`. It talks only to deputy's gateway on `127.0.0.1`, which answers only to a
  random per-run token and forwards only to your configured local backend.
- **No hooks, no MCP servers, no settings files** of yours or of the repository are loaded. The
  agent's conversations are kept in a folder of the run, removed with it, never in your own Claude
  Code history.
- **Text in files is data.** The agent is told so; the confinement above is what holds if a file
  tries to instruct it.
- **`command` checks run the agent's code, in a sandbox.** Tests and linters in `checks` run on
  the copy after the agent ends. deputy runs them in its own sandbox (`sandbox-exec` on macOS,
  bubblewrap on Linux): writes only in the copy and its temporary folder, no reading of `~/.ssh`,
  `~/.aws`, `~/.gnupg`, `~/.kube`, `~/.docker`, `~/.config`, `~/.claude` or the credential files
  in the home folder, and no network beyond localhost (test suites start local servers; on Linux
  there is no network at all). deputy uses the sandbox only if it starts: bubblewrap is often
  installed where the system refuses it. Inside that sandbox a check needs no entry in
  `policy.allowed_commands`: it can do no more than the agent's own confined shell. If you set
  `policy.command_wrapper` yourself, your wrapper and your allow-list apply instead; with no
  sandbox tool on the machine the allow-list applies, and a command it does not allow is refused
  before the run starts, not after an hour of work.

Measured on 2026-11-19: asked to write and read outside its copy, read `~/.ssh/config` and run
`cat ~/.ssh/config`, the agent was refused every time; a probe script found no network and no
writable home folder.

## Review of the sandbox (2026-11-18)

A written review of the three ways a worker touches files (`read_file`, `write_file`, `grep`),
done for issue [#23](https://github.com/tommasomarchionni/deputy/issues/23).

| Finding | Severity | State |
| --- | --- | --- |
| `grep` followed a symlink inside the folder that points outside and returned the outside file's lines | high (needs such a link in the folder) | **fixed**; every candidate file is now resolved and must stay inside the root |
| `write_file` through a hard link would overwrite a file outside the folder | medium | **fixed**: a hard-linked file is never written |
| A symlink swapped in between the check and the use (TOCTOU) | low | **fixed**: files are opened without following a final symlink, and checked on the open descriptor |
| A FIFO or device in the folder could block the server; a huge file could exhaust memory | low | **fixed**: only regular files are read, up to 5 MB |
| Secret-looking names missed `.git`, `.ssh`, `.aws`, `credentials*`, `.pypirc`, `*.keystore`, `secrets.*` | medium | **fixed** |
| A search pattern can take exponential time | medium | **fixed** ([#48](https://github.com/tommasomarchionni/deputy/issues/48)): each line match has a 50 ms limit, a search has a 5 s budget, patterns are capped at 200 characters and lines at 2,000 |
| A `command` check runs worker-written code with the user's rights | high by design | **mitigated** ([#49](https://github.com/tommasomarchionni/deputy/issues/49)): `policy.command_wrapper` runs it in an OS sandbox (`deputy init --confine`); without it the risk stands, and `deputy doctor` says so |

All of these are in current releases: the `grep` symlink fix since 0.3.0, the search time limits
since 0.6.1. Run the latest version (`deputy update`).

Worker tools added after this review go through the same path and secret checks: `list_files`,
`summarise_log` (both read through the sandbox), and read-only `git`, which also has its own
allow-list, a clean environment and pathspec exclusion of secret files, each with tests.

Report vulnerabilities privately: see [SECURITY.md](https://github.com/tommasomarchionni/deputy/blob/main/SECURITY.md).

## Confining `command` checks

A `command` check may run a file the worker just wrote (a test, a script), so **without a wrapper
the worker can run code with your rights**. `deputy doctor` says plainly whether commands are
confined. To confine them, set `policy.command_wrapper`, an argv prefix put in front of every
command; `{workdir}` is replaced by the check's working directory. `deputy init --confine` writes a
ready-made one for your platform:

| Platform | Wrapper | What it allows |
| --- | --- | --- |
| macOS | `sandbox-exec` with a profile written to `~/.config/deputy/command-sandbox.sb` | read anywhere except `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.config`; write only in the work folder, `/private/tmp` and the user temp folders; no network. Verified on macOS (the test runs a real command under it) |
| Linux | `bwrap` (bubblewrap) | a read-only view of the system, write only in the work folder and a private `/tmp`, no network. Written from the bubblewrap documentation, **not verified on real hardware yet** |
| Windows | none | commands are not confined; leave `allowed_commands` empty |

The allow-list still judges the command you declared, not the wrapper. If the wrapper program is
missing, the check fails instead of running unconfined. The wrapper reduces the damage a worker's
code can do; it is not a promise of perfect isolation: keep `allowed_commands` short.
