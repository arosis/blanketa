#!/bin/bash
# Test PKI for signing without a real certificate: an untrusted CA, one signing certificate and a macOS keychain
# holding it. Output goes to pki/ (or the directory given). Never use these for anything but tests.
#   tools/make-test-pki.sh [DIR]
# The keychain password is "test" (app.py's default ZFO_TEST_KEYCHAIN_PASSWORD).
set -euo pipefail
cd "$(dirname "$0")/.."
DIR="${1:-pki}"
mkdir -p "$DIR"
cnf="$PWD/pki/ca.cnf"
cd "$DIR"

openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 365 -config "$cnf" -extensions v3_ca
openssl req -newkey rsa:2048 -nodes -keyout user.key -out user.csr \
  -subj "/C=CZ/O=Blanketa TEST/CN=Testovaci Podepisujici/serialNumber=TEST0001"
openssl x509 -req -in user.csr -CA ca.crt -CAkey ca.key -CAcreateserial -out user.crt -days 365 \
  -extfile "$cnf" -extensions v3_user
# 3DES/SHA-1 so that older Windows versions can import it as well.
openssl pkcs12 -export -inkey user.key -in user.crt -certfile ca.crt -out user.pfx -passout pass:test \
  -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1

rm -f test.keychain-db
security create-keychain -p test "$PWD/test.keychain-db"
security set-keychain-settings "$PWD/test.keychain-db"
security import user.pfx -k "$PWD/test.keychain-db" -P test -T /usr/bin/security
security set-key-partition-list -S apple-tool:,apple: -s -k test "$PWD/test.keychain-db" >/dev/null
echo "test PKI in $PWD"
