# Security policy

## Supported versions

deputy is alpha (0.x). Security fixes go into the next release; only the latest release is
supported. Update with `deputy update`.

## Reporting a vulnerability

Please do **not** open a public issue. Report it privately through GitHub:
<https://github.com/tommasomarchionni/deputy/security/advisories/new>
(Security → Report a vulnerability). Include what you found, how to reproduce it, and the impact
you expect. Do not include real secrets or private code.

What to expect:

- an acknowledgement within 3 days;
- a first assessment (accepted or not, and why) within 7 days;
- for an accepted report, a fix released as soon as it is ready, a GitHub security advisory that
  credits you if you wish, and a note in the release.

Please give us a reasonable time to release a fix before you disclose the issue publicly.

## What counts as a vulnerability here

deputy runs model-written output inside a sandbox, so these matter most: a worker writing or reading
outside its sandbox, a symlink or path-traversal escape, a secret file read through any worker tool
(including the read-only `git`), a check command that runs something not on the allow-list, a secret
reaching a prompt, a log or a trace, prompt injection that changes what the server does, and a tool
result that makes the orchestrator run something it should not.

## How deputy is protected

- The security model and the sandbox review: <https://tommasomarchionni.github.io/deputy/security/>
- Releases are built by GitHub Actions and carry build provenance. Check a wheel with
  `gh attestation verify deputy-<version>-py3-none-any.whl -R tommasomarchionni/deputy`; the
  provenance bundle is also attached to each release as `<wheel>.intoto.jsonl`.
- `deputy update` verifies that provenance before installing.
