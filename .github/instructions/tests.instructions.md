---
description: Writing and running tests
applyTo: 'tests/**'
paths: 'tests/**'
---

# Tests

| Kind | Where | Rule |
| --- | --- | --- |
| Unit | `tests/test_*.py` | one rule per test; no network (sockets are disabled); no real clock |
| Property | `tests/test_properties.py` | hypothesis, for guarantees that hold for any input |
| Snapshot | `tests/test_public_surface.py` | tool schemas, CLI help, report format; update on purpose |
| Integration | `tests/integration/` | a real socket, process or protocol; only the model is faked |
| Automation | `tests/ci/` | the rules of `tools/ci/` scripts |
| Live | `tests/live/` | a real model; skipped unless `DEPUTY_LIVE_URL` is set |

- A bug gets a test that fails before the fix and passes after; say so in the pull request.
- Test behaviour (inputs and outcomes), not implementation. A name states the rule:
  `test_a_symlink_that_points_outside_is_refused`, not `test_sandbox_3`.
- Tests run in random order and each must be independent. Use `tmp_path`, never shared state.
- Build model replies with `tests/helpers.py`; use `fake_client` for scripted runs.
- Do not update a snapshot to make a test pass: read the diff, and update only if the public surface
  was meant to change.
- No skips, retries, longer timeouts or lowered coverage to hide a defect.
