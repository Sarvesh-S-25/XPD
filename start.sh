#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  exec python3 -m promptmeter "$@"
elif command -v python >/dev/null 2>&1; then
  exec python -m promptmeter "$@"
else
  echo "Python 3.9+ is required but was not found on this machine." >&2
  exit 1
fi
