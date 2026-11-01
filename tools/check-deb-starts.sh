#!/bin/sh
# Runs as root inside a clean container with the repository in /repo: installs the built .deb, and fails unless the app
# is still running after the wait. Chromium refuses to start as root, so the app runs under its own user.
set -eu

export DEBIAN_FRONTEND=noninteractive LC_ALL=C.UTF-8
apt-get update -qq
apt-get install -y -qq /repo/dist/blanketa_*_amd64.deb xvfb > /dev/null
useradd -m tester
Xvfb :9 -screen 0 1400x1000x24 > /dev/null 2>&1 &
sleep 3

status=0
su tester -c 'cd && DISPLAY=:9 timeout 40 blanketa /repo/samples/cssz-onz/form.zfo' || status=$?
if [ "$status" -ne 124 ]; then
  echo "Blanketa ended with status $status instead of still running"
  exit 1
fi
