#!/bin/bash
# Locks deps/*.in into requirements*.txt with hashes, for every platform at once, keeping the versions already locked.
# Run it after changing deps/*.in; needs uv (pip install uv). Add --upgrade to move everything to the newest versions.
# Dependabot updates the locked versions and hashes in requirements*.txt directly.
set -euo pipefail
cd "$(dirname "$0")/.."
compile() {
  uv pip compile --quiet --universal --python-version 3.12 --generate-hashes --no-strip-markers \
    --custom-compile-command tools/lock-requirements.sh "$@"
}
compile deps/runtime.in -o requirements.txt "$@"
compile deps/desktop.in -c requirements.txt -o requirements-desktop.txt "$@"
compile deps/ci.in -c requirements.txt -o requirements-ci.txt "$@"
compile deps/fuzz.in -c requirements.txt -o requirements-fuzz.txt "$@"
compile deps/build.in -c requirements-desktop.txt -o requirements-build.txt "$@"
