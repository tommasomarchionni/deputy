"""The `deputy` command."""

from __future__ import annotations

import argparse
import asyncio
import sys
from importlib import metadata
from pathlib import Path

from deputy import install as installer
from deputy.backends import ChatClient
from deputy.config import (
    ConfigError,
    config_path,
    load_config,
    state_dir,
)
from deputy.profiles import load_profiles

CONFIG_TEMPLATE = """# deputy configuration. No secrets here: use a key file (mode 600) or env var.
default_backend = "{name}"

[[backends]]
name = "{name}"
base_url = "{url}"
model = "{model}"
kind = "local"
{key_line}
# [policy]
# allowed_commands = [["node", "--test"], ["python", "-m", "pytest"]]
"""


def _version() -> str:
    try:
        return metadata.version("deputy")
    except metadata.PackageNotFoundError:
        return "0+unknown"


def _ask(prompt: str, default: str, interactive: bool) -> str:
    if not interactive:
        return default
    answer = input(f"{prompt} [{default}]: ").strip()
    return answer or default


def cmd_init(args: argparse.Namespace) -> int:
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


async def _doctor() -> int:
    print(f"deputy {_version()}  python {sys.version.split()[0]}")
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"config: FAIL - {exc}")
        return 1
    print(f"config: ok ({config_path()})")
    failed = 0
    for backend in config.backends:
        client = ChatClient(backend)
        try:
            ok, detail = await client.health()
        except ConfigError as exc:
            ok, detail = False, str(exc)
        finally:
            await client.aclose()
        marker = "ok" if ok else "FAIL"
        print(f"backend {backend.name}: {marker} - {detail}")
        failed += 0 if ok else 1
    print(f"profiles: {', '.join(load_profiles(Path.cwd()))}")
    try:
        state_dir().mkdir(parents=True, exist_ok=True)
        print(f"state: ok ({state_dir()})")
    except OSError as exc:
        print(f"state: FAIL - {exc.strerror}")
        failed += 1
    return 1 if failed else 0


def cmd_doctor(_: argparse.Namespace) -> int:
    return asyncio.run(_doctor())


def cmd_install(args: argparse.Namespace) -> int:
    print(installer.snippet(args.agent))
    if args.apply:
        try:
            print(installer.apply(args.agent, Path.cwd()))
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    else:
        print("\n(printed only; add --apply to do it for you)")
    return 0


def cmd_instructions(args: argparse.Namespace) -> int:
    if args.write:
        print(installer.write_instructions(Path(args.write)))
    else:
        print(installer.instructions_text())
    return 0


def cmd_mcp(_: argparse.Namespace) -> int:
    from deputy.server import build_server  # noqa: PLC0415  (slow import, only for this command)

    build_server().run("stdio")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deputy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"deputy {_version()}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="write a starter configuration")
    init.add_argument("--name", default="local")
    init.add_argument("--url")
    init.add_argument("--model")
    init.add_argument("--key-file")
    init.add_argument("--force", action="store_true")
    init.add_argument("--yes", action="store_true", help="never ask; use the flags and defaults")
    init.set_defaults(func=cmd_init)

    sub.add_parser("doctor", help="check configuration and backends").set_defaults(func=cmd_doctor)

    inst = sub.add_parser("install", help="connect deputy to an agent")
    inst.add_argument("agent", choices=installer.AGENTS)
    inst.add_argument("--apply", action="store_true", help="do it instead of printing")
    inst.set_defaults(func=cmd_install)

    ins = sub.add_parser("instructions", help="print or write the delegation guide for agents")
    ins.add_argument("--write", metavar="FILE", help="insert it in FILE (CLAUDE.md, ...)")
    ins.set_defaults(func=cmd_instructions)

    sub.add_parser("mcp", help="run the MCP server on stdio").set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
