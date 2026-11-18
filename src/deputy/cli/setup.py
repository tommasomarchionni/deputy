"""First-run commands: `init` writes a configuration, `doctor` checks that everything works."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from deputy import confinement, updates
from deputy.backends import ChatClient
from deputy.cli import tuning
from deputy.cli._common import Subparsers
from deputy.config import Config, ConfigError, Policy, config_path, load_config, state_dir
from deputy.model_profiles import changed_model, load_profile
from deputy.profiles import load_profiles
from deputy.version import current_version

CONFIG_TEMPLATE = """# deputy configuration. No secrets here: use a key file (mode 600) or env var.
default_backend = "{name}"
# auto_tune = "propose"      # off | propose | apply (see docs/configuration.md)
# auto_update = "notify"     # off | notify | apply

[[backends]]
name = "{name}"
base_url = "{url}"
model = "{model}"
kind = "local"
{key_line}
# [policy]
# allowed_commands = [["node", "--test"], ["python", "-m", "pytest"]]
{wrapper_block}"""


def _ask(prompt: str, default: str, interactive: bool) -> str:
    if not interactive:
        return default
    return input(f"{prompt} [{default}]: ").strip() or default


def _wrapper_block(*, confine: bool) -> str:
    """The `command_wrapper` lines for the config: active with --confine, a comment otherwise."""
    wrapper = confinement.suggested_wrapper()
    if wrapper is None:
        return "# command_wrapper: no sandbox tool found (macOS: sandbox-exec, Linux: bwrap)\n"
    if sys.platform == "darwin":
        confinement.write_profile()
    line = f"command_wrapper = {json.dumps(wrapper)}"
    if confine:
        return f"[policy]\n{line}\n"
    note = "# To confine `command` checks (writes only in the work folder, no network), add:"
    return f"{note}\n# [policy]\n# {line}\n"


def init(args: argparse.Namespace) -> int:
    target = config_path()
    if target.exists() and not args.force:
        print(f"{target} already exists; use --force to overwrite", file=sys.stderr)
        return 1
    interactive = sys.stdin.isatty() and not args.yes
    url = args.url or _ask("Model server URL", "http://127.0.0.1:8080", interactive)
    model = args.model or _ask("Model name", "default", interactive)
    key_file = args.key_file or _ask("API key file (blank for none)", "", interactive)
    key_line = f'api_key_file = "{key_file}"' if key_file else ""
    wrapper_block = _wrapper_block(confine=args.confine)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        CONFIG_TEMPLATE.format(
            name=args.name, url=url, model=model, key_line=key_line, wrapper_block=wrapper_block
        ),
        encoding="utf-8",
    )
    target.chmod(0o600)
    print(f"wrote {target}\nnext: deputy doctor")
    return 0


async def _check_backends() -> tuple[int, dict[str, list[str]]]:
    """Print one line per backend; return how many are not healthy and the models served."""
    failed = 0
    served: dict[str, list[str]] = {}
    for backend in load_config().backends:
        client = ChatClient(backend)
        try:
            probe = await client.probe()
            ok, detail = probe.ok, probe.detail
            served[backend.name] = probe.models
        except ConfigError as exc:
            ok, detail = False, str(exc)
        finally:
            await client.aclose()
        print(f"backend {backend.name}: {'ok' if ok else 'FAIL'} - {detail}")
        failed += 0 if ok else 1
    return failed, served


def _model_changes(config: Config, served: dict[str, list[str]]) -> None:
    """Say when a server serves another model than the one measured, and act as `auto_tune` says.

    Only `doctor` does this, never a delegation: a re-tune takes minutes.
    """
    for backend in config.backends:
        profile = load_profile(backend)
        now = changed_model(profile, served.get(backend.name, []))
        if profile is None or now is None:
            continue
        print(f"model {backend.name}: now serves {now}, was measured on {profile.served_model}")
        if config.auto_tune == "apply":
            print("re-tuning with one quick pass (auto_tune = apply)...")
            tuning.retune(backend.name)
        elif config.auto_tune == "propose":
            print("run: deputy tune")
        else:
            print("auto_tune is off: run deputy tune when you want a new measurement")


def _check_state() -> int:
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"state: FAIL - {exc.strerror}")
        return 1
    print(f"state: ok ({state_dir()})")
    return 0


def _print_update_notice() -> None:
    try:
        auto_update = load_config().auto_update
    except ConfigError:
        return
    if auto_update == "off":
        return
    info = updates.check()
    if info.available:
        print(f"update: {info.latest} is available (you have {info.current}); run: deputy update")


def _confinement_line(policy: Policy) -> tuple[str, bool]:
    """Whether `command` checks are confined, said plainly, and whether that is a failure."""
    if policy.command_wrapper:
        program = policy.command_wrapper[0]
        if confinement.wrapper_tool(policy.command_wrapper) is None:
            return f"commands: FAIL - the wrapper program '{program}' was not found", True
        return f"commands: confined by {program}", False
    if not policy.allowed_commands:
        return "commands: no `command` check is allowed (policy.allowed_commands is empty)", False
    return (
        "commands: NOT confined - a `command` check runs worker-written code with your rights; "
        "see `deputy init --confine`",
        False,
    )


def doctor(_: argparse.Namespace) -> int:
    print(f"deputy {current_version()}  python {sys.version.split()[0]}")
    try:
        load_config()
    except ConfigError as exc:
        print(f"config: FAIL - {exc}")
        return 1
    print(f"config: ok ({config_path()})")
    failed, served = asyncio.run(_check_backends())
    profiles = load_profiles(Path.cwd())
    print(f"profiles: {', '.join(profiles)}")
    for profile in profiles.values():
        for problem in profile.problems:
            print(f"profile {profile.name}: {problem}")
    failed += _check_state()
    line, broken = _confinement_line(load_config().policy)
    print(line)
    failed += broken
    _model_changes(load_config(), served)
    _print_update_notice()
    return 1 if failed else 0


def register(sub: Subparsers) -> None:
    init_parser = sub.add_parser("init", help="write a starter configuration")
    init_parser.add_argument("--name", default="local")
    init_parser.add_argument("--url")
    init_parser.add_argument("--model")
    init_parser.add_argument("--key-file")
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument(
        "--confine", action="store_true", help="run `command` checks inside an OS sandbox"
    )
    init_parser.add_argument("--yes", action="store_true", help="never ask; use flags and defaults")
    init_parser.set_defaults(func=init)

    sub.add_parser("doctor", help="check configuration and backends").set_defaults(func=doctor)
