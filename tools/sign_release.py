"""Signs the file digests of a release folder; exits 1 unless the result verifies against the built-in keys.

    UPDATE_SIGNING_KEY=... python tools/sign_release.py <folder> <version>
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import update_signature  # noqa: E402

KEY_VARIABLE = "UPDATE_SIGNING_KEY"
KEYS_OVERRIDE_VARIABLE = "BLANKETA_RELEASE_PUBLIC_KEYS"


def main(argv, environment):
    if len(argv) != 2:
        print("Usage: sign_release.py <folder> <version>", file=sys.stderr)
        return 1
    folder, version = Path(argv[0]), argv[1]
    secret = environment.get(KEY_VARIABLE, "").strip()
    if not secret:
        print(f"{KEY_VARIABLE} is not set", file=sys.stderr)
        return 1
    override = environment.get(KEYS_OVERRIDE_VARIABLE)
    keys = tuple(key for key in override.split(",") if key) if override is not None else None
    try:
        body = update_signature.manifest(version, folder)
        signature = update_signature.sign(body, secret)
        update_signature.verified_manifest(body, signature, keys=keys)
    except ValueError as error:
        print(f"Signing failed: {error}", file=sys.stderr)
        return 1
    (folder / update_signature.MANIFEST_NAME.format(version=version)).write_bytes(body)
    (folder / update_signature.SIGNATURE_NAME.format(version=version)).write_text(signature + "\n")
    print(f"Signed {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ))
