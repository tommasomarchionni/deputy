#!/usr/bin/env bash
# Copia di lavoro per il delegato locale: il modello scrive qui, mai nel repository.
#   sandbox.sh create <nome> <percorso>...   copia i percorsi (relativi al repo) e le regole
#   sandbox.sh diff <nome>                   mostra cosa è cambiato rispetto al repository
#   sandbox.sh test <nome> <file-test>...    esegue i test nella copia (runner di Node)
#   sandbox.sh path <nome>                   stampa la cartella (il workdir da passare al tool)
# Non applica mai nulla al repository: si porta a mano, dopo la revisione.
set -euo pipefail
REPO="${REPO:-$(git rev-parse --show-toplevel)}"
BASE="${SANDBOX_BASE:-${XDG_STATE_HOME:-$HOME/.local/state}/deputy/sandboxes}"
cmd="${1:?uso: sandbox.sh create|diff|test|path <nome> ...}"; name="${2:?nome}"; shift 2
dir="$BASE/$name"
case "$cmd" in
  create)
    rm -rf "$dir"; mkdir -p "$dir"
    # sempre: regole per area, configurazione dei tipi, e un esempio di test
    for p in .github/instructions tsconfig.base.json tsconfig.json package.json "$@"; do
      [ -e "$REPO/$p" ] || continue
      mkdir -p "$dir/$(dirname "$p")"; cp -R "$REPO/$p" "$dir/$p"
    done
    # Il repository può non avere node_modules: la copia installa solo ciò che serve ai test
    # (versioni lette da package.json), senza toccare il repository.
    Z=$(grep -o '"zod": "[^"]*"' "$REPO/package.json" | cut -d'"' -f4)
    T=$(grep -o '"tsx": "[^"]*"' "$REPO/package.json" | cut -d'"' -f4)
    printf '{"name":"sandbox","private":true,"type":"module","dependencies":{"zod":"%s","tsx":"%s"}}\n' "$Z" "$T" > "$dir/package.json"
    ( cd "$dir" && npm install --no-package-lock --ignore-scripts --silent >/dev/null 2>&1 ) || echo "attenzione: npm install fallito" >&2
    printf 'node_modules\n' > "$dir/.gitignore"
    git -C "$dir" init -q && git -C "$dir" add -A >/dev/null 2>&1 && \
      git -C "$dir" -c user.name=sandbox -c user.email=s@s commit -qm base --no-verify || true
    echo "$dir" ;;
  diff) git -C "$dir" add -A -N >/dev/null 2>&1 || true; git -C "$dir" diff --stat; git -C "$dir" diff ;;
  test) ( cd "$dir" && node --import tsx --test "$@" ) ;;
  path) echo "$dir" ;;
  *) echo "comando sconosciuto"; exit 2 ;;
esac
