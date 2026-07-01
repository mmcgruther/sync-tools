#!/usr/bin/env bash
# push-to-artifactory.sh
#
# Uploads all wheel files from DOWNLOAD_ROOT to an Artifactory PyPI local repository.
# Uses HTTP PUT directly against the Artifactory REST API.
#
# Upload path layout:
#   PUT /{repo}/{package_name}/{filename}.whl
# The package name is extracted from the wheel filename (first hyphen-delimited segment).
# This is the layout Artifactory's PyPI simple index requires — files at the repo root
# are stored but not indexed, so pip cannot resolve them.
#
# Behaviour:
#   - HTTP 201/200  → UPLOADED  (counted)
#   - HTTP 409      → SKIPPED   (file already exists — safe to re-run)
#   - anything else → FAILED    (counted; script exits nonzero at end)
#
# Deduplication: if the same wheel filename appears in multiple subdirectories
# (e.g. because download-toolchain.sh was not run), only the first occurrence is uploaded.
#
# Required environment variables:
#   ARTIFACTORY_URL  — base URL, no trailing slash (e.g. https://art.example.com)
#   ART_USER         — Artifactory username
#   ART_TOKEN        — Artifactory API key or password
#   REPO             — local PyPI repo name (e.g. pypi-local)
#
# Optional environment variables:
#   DOWNLOAD_ROOT    — directory tree to scan for .whl files (default: ./toolchain)
set -euo pipefail

# Fail fast with a clear message for missing required variables.
: "${ARTIFACTORY_URL:?ARTIFACTORY_URL is required (e.g. https://art.example.com)}"
: "${ART_USER:?ART_USER is required}"
: "${ART_TOKEN:?ART_TOKEN is required}"
: "${REPO:?REPO is required (e.g. pypi-local)}"

DOWNLOAD_ROOT="${DOWNLOAD_ROOT:-./toolchain}"
ARTIFACTORY_URL="${ARTIFACTORY_URL%/}"  # strip any trailing slash

log() { echo "[push-to-artifactory] $*"; }

log "Target : ${ARTIFACTORY_URL}/artifactory/${REPO}/<package_name>/<filename>.whl"
log "Source : ${DOWNLOAD_ROOT}"
log ""

# Collect all wheel files under DOWNLOAD_ROOT, deduplicate by filename.
# Sort ensures deterministic ordering and groups duplicates together.
declare -A _seen  # bash 4.2+ associative array — standard on Linux
upload_queue=()

while IFS=$'\t' read -r fname fpath; do
    if [[ -n "${_seen[${fname}]+x}" ]]; then
        log "  DEDUP    ${fname}  (keeping ${_seen[${fname}]})"
    else
        _seen["${fname}"]="${fpath}"
        upload_queue+=("${fpath}")
    fi
done < <(
    find "${DOWNLOAD_ROOT}" -name "*.whl" \
    | while IFS= read -r f; do printf '%s\t%s\n' "$(basename "${f}")" "${f}"; done \
    | sort
)

total="${#upload_queue[@]}"
if [[ "${total}" -eq 0 ]]; then
    log "No wheel files found under ${DOWNLOAD_ROOT}. Nothing to upload."
    log "Run download-toolchain.sh first."
    exit 0
fi
log "Found ${total} unique wheel(s) to upload."
log ""

uploaded=0
skipped=0
failed=0

for fpath in "${upload_queue[@]}"; do
    fname="$(basename "${fpath}")"
    # Wheel filename format: {distribution}-{version}-{python}-{abi}-{platform}.whl
    # The distribution name (first hyphen-delimited segment) is the Artifactory
    # subdirectory.  Artifactory's PyPI local repo requires this layout:
    #   /{repo}/{package_name}/{filename}
    # Files placed at the repo root are stored but NOT indexed by the simple API,
    # so pip cannot find them.
    pkg_name="${fname%%-*}"
    url="${ARTIFACTORY_URL}/artifactory/${REPO}/${pkg_name}/${fname}"

    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        -u "${ART_USER}:${ART_TOKEN}" \
        -X PUT -T "${fpath}" \
        "${url}")

    case "${http_code}" in
        200|201)
            log "  UPLOADED  ${fname}"
            uploaded=$(( uploaded + 1 ))
            ;;
        409)
            log "  SKIPPED   ${fname} (already exists)"
            skipped=$(( skipped + 1 ))
            ;;
        *)
            log "  FAILED    ${fname} (HTTP ${http_code})"
            failed=$(( failed + 1 ))
            ;;
    esac
done

log ""
log "Summary: ${uploaded} uploaded, ${skipped} skipped, ${failed} failed  (${total} total)"

if [[ "${failed}" -gt 0 ]]; then
    log "ERROR: ${failed} upload(s) failed — check ARTIFACTORY_URL, REPO, credentials, and network."
    exit 1
fi
