#!/bin/bash
# Test PKI for signing without a real certificate: an untrusted CA, one signing certificate (user.pfx) and, on
# macOS, a keychain holding it. Windows and Linux use user.pfx (Windows can also import it into its store). Output goes to pki/ (or the directory given). Never use these for anything but tests.
#   tools/make-test-pki.sh [DIR]
# The keychain password is "test" (app.py's default ZFO_TEST_KEYCHAIN_PASSWORD).
set -euo pipefail
# Git Bash on Windows would rewrite the subject "/C=CZ/O=..." below into a file path; other paths still need it.
export MSYS2_ARG_CONV_EXCL="/C="
cd "$(dirname "$0")/.."
ROOT="$PWD"
DIR="${1:-pki}"
mkdir -p "$DIR"
cnf="$ROOT/pki/ca.cnf"
cd "$DIR"

openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 365 -config "$cnf" -extensions v3_ca
openssl req -newkey rsa:2048 -nodes -keyout user.key -out user.csr \
  -subj "/C=CZ/O=Blanketa TEST/CN=Testovaci Podepisujici/serialNumber=TEST0001"
openssl x509 -req -in user.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out user.crt -days 365 \
  -extfile "$cnf" -extensions v3_user
# 3DES/SHA-1 so that older Windows versions can import it as well.
openssl pkcs12 -export -inkey user.key -in user.crt -certfile ca.crt -out user.pfx -passout pass:test \
  -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1

if [ "$(uname)" = Darwin ]; then
  rm -f test.keychain-db
  security create-keychain -p test "$PWD/test.keychain-db"
  security set-keychain-settings "$PWD/test.keychain-db"
  security import user.pfx -k "$PWD/test.keychain-db" -P test -A
  # macsign is ad-hoc signed, so its partition is its cdhash; without it every signature waits for an
  # "allow access" dialog, which on CI nobody answers. Build macsign first, and rerun this after rebuilding it.
  partitions=apple-tool:,apple:
  if [ -x "$ROOT/macsign/macsign" ]; then
    partitions="$partitions,cdhash:$(codesign -dvvv "$ROOT/macsign/macsign" 2>&1 | sed -n 's/^CDHash=//p')"
  fi
  security set-key-partition-list -S "$partitions" -s -k test "$PWD/test.keychain-db" >/dev/null
fi
echo "test PKI in $PWD"
