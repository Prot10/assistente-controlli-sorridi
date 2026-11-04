#!/bin/bash
# Pubblica la versione scritta in package.json:
#   1. tag e release su GitHub (il tag fa partire la build per Windows, .github/workflows/release.yml);
#   2. app per Mac costruita qui, firmata con il Developer ID e notarizzata, caricata nella stessa release.
# Le app installate trovano la nuova versione da sole (electron-updater legge latest.yml e latest-mac.yml).
#
# Serve un file con APPLE_ID, APPLE_PASSWORD (password specifica per app) e APPLE_TEAM_ID: .env (escluso da git)
# oppure quello indicato in ENV_FILE. Le password restano nell'ambiente del processo: questo script non le stampa.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSIONE="v$(node -p 'require("./package.json").version')"
[ -z "$(git status --porcelain)" ] || { echo "Ci sono modifiche non committate: committa prima di pubblicare."; exit 1; }
ENV_FILE="${ENV_FILE:-.env}"
[ -f "$ENV_FILE" ] || { echo "Manca $ENV_FILE con le credenziali Apple."; exit 1; }
set -a; source "$ENV_FILE"; set +a
export APPLE_APP_SPECIFIC_PASSWORD="$APPLE_PASSWORD"
export GH_TOKEN="$(gh auth token --user Prot10)"
export PYTHON="${PYTHON:-.venv/bin/python}"
# Nel portachiavi ci sono due Developer ID con lo stesso nome: si sceglie per impronta (la stessa di Kosmico).
export CSC_NAME="${CSC_NAME:-E1513974CD376A7B5355D3EB3E90DDFED265943B}"

if ! git rev-parse -q --verify "refs/tags/$VERSIONE" >/dev/null; then
  PRECEDENTE="$(git describe --tags --abbrev=0 2>/dev/null || true)"
  NOTE="$(mktemp)"
  git log --format='- %s' ${PRECEDENTE:+$PRECEDENTE..}HEAD > "$NOTE"
  git tag -a "$VERSIONE" -m "Assistente controlli $VERSIONE"
  git push origin HEAD "$VERSIONE"
  gh release create "$VERSIONE" --verify-tag --title "Assistente controlli $VERSIONE" --notes-file "$NOTE"
fi

npm run backend
npm run codex
npx electron-builder --mac --publish always
xcrun stapler validate "dist/assistente-controlli-${VERSIONE#v}-mac-arm64.dmg"
echo "Mac pubblicato. Windows: gh run watch --repo Prot10/assistente-controlli-sorridi"
