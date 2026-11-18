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
| A search pattern can take exponential time | medium | open: [#48](https://github.com/tommasomarchionni/deputy/issues/48) |
| A `command` check runs worker-written code with the user's rights | high by design | open: [#49](https://github.com/tommasomarchionni/deputy/issues/49); until then use a workspace copy and a throwaway environment |

The fixes ship in the next release. Versions 0.1.0 and 0.2.0 have the `grep` flaw: do not point
deputy at a folder that contains symlinks to files outside it until you update (`deputy update`).

Report vulnerabilities privately: see [SECURITY.md](https://github.com/tommasomarchionni/deputy/blob/main/SECURITY.md).
