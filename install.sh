#!/bin/sh
# deputy installer.
#   curl -LsSf https://raw.githubusercontent.com/tommasomarchionni/deputy/main/install.sh | sh
#
# What it does: installs the `deputy` command with `uv tool` (an isolated environment, no sudo,
# nothing outside ~/.local). It never edits your agent's configuration: run `deputy install <agent>`
# for that. Read this file before you pipe it into a shell; it is short on purpose.
#
# Options (after `sh -s --` when piped):
#   --uninstall     remove deputy
#   --install-uv    also install uv if it is missing (runs the official astral.sh installer)
# Environment:
#   DEPUTY_REF      git branch, tag or commit to install from source instead of a release
#   DEPUTY_SOURCE   install from this local path or URL instead of GitHub (used by CI)
set -eu

REPO_URL="https://github.com/tommasomarchionni/deputy"

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# The wheel of the latest release (built and attested by the release workflow), or nothing.
latest_wheel() {
  have curl || return 1
  curl -fsSL --max-time 5 "https://api.github.com/repos/tommasomarchionni/deputy/releases/latest" 2>/dev/null |
    sed -n 's/.*"browser_download_url": *"\([^"]*-py3-none-any\.whl\)".*/\1/p' | head -n 1
}

ensure_uv() {
  have uv && return 0
  if [ "${INSTALL_UV:-0}" = 1 ]; then
    say "installing uv (https://astral.sh/uv) ..."
    have curl || fail "curl is needed to install uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck disable=SC1091
    [ -f "$HOME/.local/bin/env" ] && . "$HOME/.local/bin/env"
    PATH="$HOME/.local/bin:$PATH"
    have uv || fail "uv was installed but is not on PATH; open a new shell and run this again"
    return 0
  fi
  fail "uv is required. Install it (https://docs.astral.sh/uv/) or re-run with --install-uv"
}

main() {
  ACTION=install
  INSTALL_UV=0
  for arg in "$@"; do
    case "$arg" in
      --uninstall) ACTION=uninstall ;;
      --install-uv) INSTALL_UV=1 ;;
      -h | --help) sed -n '2,16p' "$0" 2>/dev/null || true; exit 0 ;;
      *) fail "unknown option: $arg" ;;
    esac
  done

  ensure_uv

  if [ "$ACTION" = uninstall ]; then
    uv tool uninstall deputy
    say "deputy removed. Your config (~/.config/deputy) and traces (~/.local/state/deputy) are kept."
    exit 0
  fi

  if [ -n "${DEPUTY_SOURCE:-}" ]; then
    SOURCE="$DEPUTY_SOURCE"
  elif [ -n "${DEPUTY_REF:-}" ]; then
    SOURCE="git+${REPO_URL}@${DEPUTY_REF}"
  else
    SOURCE="$(latest_wheel || true)"
    [ -n "$SOURCE" ] || SOURCE="git+${REPO_URL}@main"
  fi
  say "installing deputy from ${SOURCE} ..."
  uv tool install --force "$SOURCE"

  # uv puts tools in its own bin directory; make sure the user can find it.
  BIN_DIR="$(uv tool dir --bin 2>/dev/null || true)"
  case ":$PATH:" in
    *":${BIN_DIR}:"*) ;;
    *) [ -n "$BIN_DIR" ] && { say "note: add ${BIN_DIR} to your PATH (uv tool update-shell does it)"; PATH="$BIN_DIR:$PATH"; } ;;
  esac

  have deputy || fail "deputy was installed but is not on PATH"
  say ""
  deputy --version
  say ""
  say "next:"
  say "  1. deputy init        # point it at your model server"
  say "  2. deputy doctor      # check that it answers"
  say "  3. deputy install claude-code   # or: vscode (Copilot), cursor, codex"
}

main "$@"
