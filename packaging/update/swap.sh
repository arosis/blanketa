#!/bin/sh
set -u
trap '' HUP

SECONDS_TO_WAIT_FOR_EXIT=120
POLLS_PER_SECOND=4
SECONDS_PER_POLL=0.25

is_absolute() {
  case "$1" in
    /*) return 0 ;;
    *) return 1 ;;
  esac
}

is_release_version() {
  case "$1" in
    "" | *[!0-9.]*) return 1 ;;
  esac
  printf '%s\n' "$1" | grep -Eqx '[0-9]+\.[0-9]+\.[0-9]+'
}

is_process_id() {
  case "$1" in
    "" | *[!0-9]* | 0*) return 1 ;;
  esac
}

if [ "$#" -lt 6 ]; then
  exit 2
fi
pid=$1 staged=$2 target=$3 updates=$4 version=$5 relaunch=$6
shift 6
is_process_id "$pid" || exit 2
is_release_version "$version" || exit 2
is_absolute "$staged" || exit 2
is_absolute "$target" || exit 2
is_absolute "$updates" || exit 2
for file do
  shift
  if is_absolute "$file"; then set -- "$@" "$file"; fi
done

trap 'rm -f -- "$updates/install.lock"' EXIT

old="$target.old-$version"
if [ "$(uname)" = Darwin ]; then
  program="Contents/MacOS/Blanketa"
else
  program="Blanketa"
fi

relaunch_app() {
  [ "$relaunch" = 1 ] || return 0
  if [ "$(uname)" = Darwin ]; then
    if [ "$#" -gt 0 ]; then set -- open -a "$target" "$@"; else set -- open "$target"; fi
  else
    set -- "$target/Blanketa" "$@"
  fi
  if [ -n "${BLANKETA_UPDATE_RELAUNCH_RECORD:-}" ]; then
    (IFS="$(printf '\t')"; printf '%s\n' "$*") >> "$BLANKETA_UPDATE_RELAUNCH_RECORD"
    return 0
  fi
  nohup "$@" > /dev/null 2>&1 &
}

record_failure() {
  printf '{"version": "%s"}\n' "$version" > "$updates/failed.json"
  rm -f -- "$updates/ready.json"
}

give_up() {
  record_failure
  relaunch_app "$@"
  exit 1
}

restore_and_give_up() {
  if [ -e "$target" ] && [ ! -e "$staged" ]; then
    mv -- "$target" "$staged" 2> /dev/null || rm -rf -- "$target"
  fi
  if [ ! -e "$target" ]; then
    mv -- "$old" "$target"
  fi
  give_up "$@"
}

polls_left=$((SECONDS_TO_WAIT_FOR_EXIT * POLLS_PER_SECOND))
while kill -0 "$pid" 2> /dev/null; do
  if [ "$polls_left" -le 0 ]; then
    record_failure
    exit 1
  fi
  polls_left=$((polls_left - 1))
  sleep "$SECONDS_PER_POLL"
done

if [ -e "$old" ]; then
  if [ ! -e "$target" ] && [ -f "$old/$program" ]; then
    mv -- "$old" "$target"
    give_up "$@"
  fi
  [ -f "$target/$program" ] || give_up "$@"
  rm -rf -- "$old" || give_up "$@"
fi
[ -f "$staged/$program" ] || give_up "$@"
mv -- "$target" "$old" || give_up "$@"
mv -- "$staged" "$target" || restore_and_give_up "$@"
[ -f "$target/$program" ] || restore_and_give_up "$@"

if [ "$(uname)" = Darwin ] && xattr -p com.apple.quarantine "$target" > /dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$target" 2> /dev/null || true
fi
rm -rf -- "$old"
rmdir -- "$(dirname -- "$staged")" 2> /dev/null || true
rm -rf -- "$updates/$version"
rm -f -- "$updates/ready.json"
relaunch_app "$@"
exit 0
