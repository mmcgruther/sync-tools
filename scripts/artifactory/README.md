# Artifactory Toolchain Seeding

Scripts for downloading the Python build toolchain from PyPI and rehosting it to a
private Artifactory PyPI local repository.  Run these from an internet-connected
machine; the resulting Artifactory repository can then be used as the sole package
source on air-gapped CI runners.

## Toolchain contents

| Package | Role | Wheel type |
|---|---|---|
| `uv` | Build frontend and package manager | Platform binary |
| `setuptools` | Build backend | Pure Python |
| `wheel` | `bdist_wheel` builder | Pure Python |
| `pytest` | Test runner | Pure Python |
| `pytest-cov` | Coverage plugin for pytest | Pure Python |
| `coverage` | Coverage engine | Platform binary (C extension) |
| `twine` | Upload wheels to Artifactory | Pure Python |

Transitive dependencies are downloaded automatically.

## When to run

| Trigger | Action |
|---|---|
| Initial setup of a new Artifactory instance | Run `seed-toolchain.sh` |
| Adding a new package to the toolchain | Add it to `PACKAGES` in `download-toolchain.sh`, re-run |
| Python version bump on CI (e.g. 3.11 → 3.12) | Re-run with the new `PYTHON_VERSION` |
| Upgrading a specific package to a newer version | Delete the old wheel from Artifactory, re-run |

## Required environment variables

### Upload (push-to-artifactory.sh / seed-toolchain.sh)

| Variable | Description | Example |
|---|---|---|
| `ARTIFACTORY_URL` | Base URL of your Artifactory instance, no trailing slash | `https://art.example.com` |
| `ART_USER` | Artifactory username | `ci-bot` |
| `ART_TOKEN` | Artifactory API key or password | *(from secret manager)* |
| `REPO` | Name of the local PyPI repository | `pypi-local` |

### Download (download-toolchain.sh / seed-toolchain.sh)

| Variable | Default | Description |
|---|---|---|
| `PYTHON_VERSION` | `3.11` | CPython version for resolving ABI-specific wheels |
| `DEST_ROOT` | `./toolchain` | Local staging directory for downloaded wheels |
| `DOWNLOAD_ROOT` | same as `DEST_ROOT` | Directory that push-to-artifactory.sh reads from |

**No credentials are ever hardcoded.**  Pass them via environment variables, a secrets
manager, or CI secret injection.

## Running the scripts

Make the scripts executable once:

```bash
chmod +x scripts/artifactory/*.sh
```

### Option A — seed in one step

```bash
export ARTIFACTORY_URL="https://art.example.com"
export ART_USER="ci-bot"
export ART_TOKEN="$(cat ~/.secrets/art-token)"
export REPO="pypi-local"
export PYTHON_VERSION="3.11"   # optional, default is 3.11

scripts/artifactory/seed-toolchain.sh
```

### Option B — download and push separately

Download first (requires internet access, no Artifactory credentials needed):

```bash
export PYTHON_VERSION="3.11"
export DEST_ROOT="./toolchain"

scripts/artifactory/download-toolchain.sh
```

Inspect the staging directory, then push:

```bash
export ARTIFACTORY_URL="https://art.example.com"
export ART_USER="ci-bot"
export ART_TOKEN="$(cat ~/.secrets/art-token)"
export REPO="pypi-local"
export DOWNLOAD_ROOT="./toolchain"   # must match DEST_ROOT above

scripts/artifactory/push-to-artifactory.sh
```

### Seeding multiple Python versions

Run the download step once per version; the push step is idempotent (HTTP 409 = skip):

```bash
for pyver in 3.11 3.12 3.13; do
    PYTHON_VERSION="${pyver}" DEST_ROOT="./toolchain-${pyver}" \
        scripts/artifactory/download-toolchain.sh
    DOWNLOAD_ROOT="./toolchain-${pyver}" \
        scripts/artifactory/push-to-artifactory.sh
done
```

## Staged directory layout (local)

```
toolchain/
  any/      — pure-Python wheels (platform-independent)
  linux/    — manylinux_2_17_x86_64 binaries (uv, coverage)
  windows/  — win_amd64 binaries (uv, coverage)
```

Pure-Python wheels that pip downloads into every platform directory are automatically
moved to `any/` and deduplicated so they are only uploaded to Artifactory once.

## Artifactory repository layout

Artifactory's PyPI local repo requires wheels to live in a `/{package_name}/`
subdirectory so they appear in the PEP 503 simple index.  The push script uploads
each file to:

```
PUT /artifactory/{REPO}/{package_name}/{filename}.whl
```

The package name is the first hyphen-delimited segment of the wheel filename
(e.g. `pytest` from `pytest-8.3.5-py3-none-any.whl`, `pytest_cov` from
`pytest_cov-5.0.0-py3-none-any.whl`).

After a successful push you should see this tree in the Artifactory UI:

```
{REPO}/
  coverage/
    coverage-7.x.y-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
    coverage-7.x.y-cp311-cp311-win_amd64.whl
  pytest/
    pytest-8.x.y-py3-none-any.whl
  uv/
    uv-0.x.y-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl
    uv-0.x.y-py3-none-win_amd64.whl
  …
```

Artifactory auto-generates the `.pypi/` virtual folder with per-package HTML files
(the PEP 503 simple index).  That folder is read-only metadata — do not upload to it.

> **If wheels landed at the repo root** (flat layout), delete them and re-run
> `push-to-artifactory.sh`.  Artifactory stores root-level files but does not include
> them in the simple index, so `pip install` cannot resolve them.

## Verifying packages landed in Artifactory

**Browse the simple index:**

```
${ARTIFACTORY_URL}/artifactory/api/pypi/${REPO}/simple/
```

**Check a specific package:**

```bash
curl -u "${ART_USER}:${ART_TOKEN}" \
    "${ARTIFACTORY_URL}/artifactory/api/pypi/${REPO}/simple/uv/"
```

**Smoke-test with pip:**

```bash
pip install uv \
    --index-url "${ARTIFACTORY_URL}/artifactory/api/pypi/${REPO}/simple/" \
    --no-deps \
    --dry-run
```

**Smoke-test with uv:**

```bash
uv pip install uv \
    --index-url "${ARTIFACTORY_URL}/artifactory/api/pypi/${REPO}/simple/" \
    --no-deps \
    --dry-run
```

## Platform notes

### Linux runners

The scripts target `manylinux_2_17_x86_64` (glibc ≥ 2.17), which covers all modern
RHEL/CentOS/Ubuntu/Debian-based CI images.  Platform wheels are also tagged
`manylinux2014_x86_64` by convention — Artifactory serves both tags correctly.

### Windows runners

`win_amd64` covers 64-bit Windows, which is the standard GitHub Actions
`windows-latest` runner architecture.

### Alpine / musl Linux (future)

If CI moves to Alpine-based images, add a `musllinux_1_2_x86_64` download pass:

```bash
pip download \
    --python-version "${PYTHON_VERSION}" \
    --implementation cp \
    --only-binary :all: \
    --platform musllinux_1_2_x86_64 \
    --dest "${DEST_ROOT}/musl" \
    uv coverage
```

Then run `dedupe_pure_python "${DEST_ROOT}/musl"` and push `${DEST_ROOT}/musl/` to
Artifactory.  The pure-Python wheels already in `any/` cover musl without changes.

### macOS runners (future)

Use `--platform macosx_12_0_x86_64` (Intel) or `--platform macosx_12_0_arm64`
(Apple Silicon / M-series) as needed.
