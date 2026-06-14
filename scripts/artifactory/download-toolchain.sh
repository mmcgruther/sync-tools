#!/usr/bin/env bash
# download-toolchain.sh
#
# Downloads all Python build toolchain wheels for offline rehosting to Artifactory.
#
# Output layout:
#   ${DEST_ROOT}/any/     — pure-Python wheels (py3-none-any, py2.py3-none-any, …)
#   ${DEST_ROOT}/linux/   — Linux-only binaries (uv, coverage for manylinux_2_17_x86_64)
#   ${DEST_ROOT}/windows/ — Windows-only binaries (uv, coverage for win_amd64)
#
# Environment variables:
#   PYTHON_VERSION  — CPython version to resolve ABI-specific wheels (default: 3.11)
#   DEST_ROOT       — output directory root (default: ./toolchain)
set -euo pipefail

PYTHON_VERSION="${PYTHON_VERSION:-3.11}"
DEST_ROOT="${DEST_ROOT:-./toolchain}"

LINUX_PLATFORM="manylinux_2_17_x86_64"
WIN_PLATFORM="win_amd64"

# Full toolchain needed by CI:
#   uv           – build frontend (platform binary)
#   setuptools   – build backend
#   wheel        – bdist_wheel builder
#   pytest       – test runner
#   pytest-cov   – coverage plugin for pytest
#   coverage     – coverage engine (platform binary — has C extension)
#   twine        – publishes wheels to Artifactory
#
# pip resolves transitive dependencies automatically.
PACKAGES=(
    uv
    setuptools
    wheel
    pytest
    pytest-cov
    coverage
    twine
)

log() { echo "[download-toolchain] $*"; }

log "Python version : ${PYTHON_VERSION}"
log "Output root    : ${DEST_ROOT}"

mkdir -p "${DEST_ROOT}/any" "${DEST_ROOT}/linux" "${DEST_ROOT}/windows"

log ""
log "--- Step 1/3: Downloading Linux (${LINUX_PLATFORM}) packages ---"
pip download \
    --python-version "${PYTHON_VERSION}" \
    --implementation cp \
    --only-binary :all: \
    --platform "${LINUX_PLATFORM}" \
    --dest "${DEST_ROOT}/linux" \
    "${PACKAGES[@]}"

log ""
log "--- Step 2/3: Downloading Windows (${WIN_PLATFORM}) packages ---"
pip download \
    --python-version "${PYTHON_VERSION}" \
    --implementation cp \
    --only-binary :all: \
    --platform "${WIN_PLATFORM}" \
    --dest "${DEST_ROOT}/windows" \
    "${PACKAGES[@]}"

log ""
log "--- Step 3/3: Deduplicating pure-Python wheels into any/ ---"
# Wheels tagged *-none-any.whl (py3-none-any, py2.py3-none-any, etc.) are pure Python and
# platform-independent.  pip downloads them into every platform dir alongside the
# platform-specific wheels; move the first copy into any/ and delete the rest.
dedupe_pure_python() {
    local src="$1"
    local moved=0 dupes=0

    while IFS= read -r -d '' f; do
        fname="$(basename "${f}")"
        if [[ -f "${DEST_ROOT}/any/${fname}" ]]; then
            rm "${f}"
            dupes=$(( dupes + 1 ))
        else
            mv "${f}" "${DEST_ROOT}/any/"
            moved=$(( moved + 1 ))
        fi
    done < <(find "${src}" -maxdepth 1 -name "*-none-any.whl" -print0)

    log "  $(basename "${src}")/: ${moved} moved, ${dupes} duplicate(s) removed"
}

dedupe_pure_python "${DEST_ROOT}/linux"
dedupe_pure_python "${DEST_ROOT}/windows"

any_count=$(find "${DEST_ROOT}/any"     -maxdepth 1 -name "*.whl" | wc -l | tr -d ' ')
linux_count=$(find "${DEST_ROOT}/linux"   -maxdepth 1 -name "*.whl" | wc -l | tr -d ' ')
win_count=$(find "${DEST_ROOT}/windows" -maxdepth 1 -name "*.whl" | wc -l | tr -d ' ')

log ""
log "Download complete."
log "  any/     : ${any_count} pure-Python wheel(s)"
log "  linux/   : ${linux_count} Linux-specific wheel(s)  (${LINUX_PLATFORM})"
log "  windows/ : ${win_count} Windows-specific wheel(s) (${WIN_PLATFORM})"
