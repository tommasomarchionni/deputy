# Security model

- **The worker is untrusted.** It can only call `read_file`, `write_file` and `grep`, all confined
  to `workdir` with `realpath` checks: `..`, absolute paths and symlinks pointing outside are
  refused. Files that look like secrets (`.env*`, `*.pem`, `*.key`, SSH keys, `.npmrc`, `.netrc`)
  are never readable or writable.
- **The worker has no shell.** Checks that run commands are declared by the orchestrator and run by
  the server, only when the argv matches `policy.allowed_commands`, without a shell, with a stripped
  environment and a timeout.
- **The orchestrator is trusted** to choose `workdir` and checks: it is the user's own agent. Point
  it at a copy (`deputy workspace create NAME PATH...`, then `deputy workspace diff NAME`) when the
  worker should not touch the real repository.
- **Worker output is a draft.** Review it before it reaches the real repository.
- **Secrets:** keys come from a mode-600 file or an environment variable, are never written to the
  config, the traces or the model prompt.
- **Hosted backends send your files to that provider.** Mark them `kind = "hosted"` and use them
  only for data you may share.
- **Traces** (`runs.jsonl`) hold run metadata and check results, not file contents or answers.

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

The fixes ship in the next release. Versions 0.1.0 and 0.2.0 have the `grep` flaw: do not point
deputy at a folder that contains symlinks to files outside it until you update (`deputy update`).

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
code can do; it is not a promise of perfect isolation: keep `allowed_commands` short and run
deputy on a workspace copy (`deputy workspace`).
