#!/bin/sh
set -eu
cd "$(dirname "$0")"
npm --prefix frontend run build
mkdir -p server/web
cp -R frontend/dist/. server/web/
exec python3 scripts/run.py
