# Security policy

## Supported versions

deputy is in early design (no release yet). Once there are releases, the latest minor version is
supported.

## Reporting a vulnerability

Please do **not** open a public issue. Use GitHub's private vulnerability reporting:
**Security → Report a vulnerability** on this repository. Include what you found, how to reproduce
it, and the impact you expect. You will get an answer within a few days.

## What counts as a vulnerability here

deputy runs model-written output inside a sandbox, so these matter most: a worker writing or reading
outside its sandbox, a symlink or path-traversal escape, a check command that runs something not on
the allow-list, a secret reaching a prompt, a log or a trace, prompt injection that changes what the
server does, and a tool result that makes the orchestrator run something it should not.
