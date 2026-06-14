#!/usr/bin/env bash
# seed-toolchain.sh
#
# Orchestrates the full toolchain seeding workflow:
#   1. Download toolchain wheels from PyPI into a local staging directory.
#   2. Upload the staged wheels to an Artifactory PyPI local repository.
#
# All configuration is via environment variables.  See scripts/artifactory/README.md
# for the full variable reference and step-by-step instructions.
#
# Download variables (passed through to download-toolchain.sh):
#   PYTHON_VERSION   — CPython version to resolve ABI wheels (default: 3.11)
#   DEST_ROOT        — staging directory for downloaded wheels (default: ./toolchain)
#
# Upload variables (passed through to push-to-artifactory.sh):
#   ARTIFACTORY_URL  — base URL of your Artifactory instance (required)
#   ART_USER         — Artifactory username (required)
#   ART_TOKEN        — Artifactory API key or password (required)
#   REPO             — local PyPI repository name (required)
#   DOWNLOAD_ROOT    — directory to upload from; defaults to DEST_ROOT / ./toolchain
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() { echo "[seed-toolchain] $*"; }

log "======================================================"
log " Toolchain seeding started"
log "======================================================"

log ""
log "=== Step 1/2: Download toolchain packages ==="
"${SCRIPT_DIR}/download-toolchain.sh"

log ""
log "=== Step 2/2: Push to Artifactory ==="
"${SCRIPT_DIR}/push-to-artifactory.sh"

log ""
log "======================================================"
log " Seeding complete"
log "======================================================"
