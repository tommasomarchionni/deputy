"""First-run commands: `init` writes a configuration, `doctor` checks that everything works."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from deputy import updates
from deputy.backends import ChatClient
from deputy.cli._common import Subparsers
from deputy.config import ConfigError, config_path, load_config, state_dir
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
"""


def _ask(prompt: str, default: str, interactive: bool) -> str:
    if not interactive:
        return default
    return input(f"{prompt} [{default}]: ").strip() or default


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
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        CONFIG_TEMPLATE.format(name=args.name, url=url, model=model, key_line=key_line),
        encoding="utf-8",
    )
    target.chmod(0o600)
    print(f"wrote {target}\nnext: deputy doctor")
    return 0


async def _check_backends() -> int:
    """Print one line per backend; return how many are not healthy."""
    failed = 0
    for backend in load_config().backends:
        client = ChatClient(backend)
        try:
            ok, detail = await client.health()
        except ConfigError as exc:
            ok, detail = False, str(exc)
        finally:
            await client.aclose()
        print(f"backend {backend.name}: {'ok' if ok else 'FAIL'} - {detail}")
        failed += 0 if ok else 1
    return failed


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


def doctor(_: argparse.Namespace) -> int:
    print(f"deputy {current_version()}  python {sys.version.split()[0]}")
    try:
        load_config()
    except ConfigError as exc:
        print(f"config: FAIL - {exc}")
        return 1
    print(f"config: ok ({config_path()})")
    failed = asyncio.run(_check_backends())
    print(f"profiles: {', '.join(load_profiles(Path.cwd()))}")
    failed += _check_state()
    _print_update_notice()
    return 1 if failed else 0


def register(sub: Subparsers) -> None:
    init_parser = sub.add_parser("init", help="write a starter configuration")
    init_parser.add_argument("--name", default="local")
    init_parser.add_argument("--url")
    init_parser.add_argument("--model")
    init_parser.add_argument("--key-file")
    init_parser.add_argument("--force", action="store_true")
    init_parser.add_argument("--yes", action="store_true", help="never ask; use flags and defaults")
    init_parser.set_defaults(func=init)

    sub.add_parser("doctor", help="check configuration and backends").set_defaults(func=doctor)
