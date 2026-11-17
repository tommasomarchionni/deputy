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

Report vulnerabilities privately: see [SECURITY.md](https://github.com/tommasomarchionni/deputy/blob/main/SECURITY.md).
