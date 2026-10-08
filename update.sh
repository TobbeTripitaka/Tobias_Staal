#!/bin/bash
# Build everything switched on in data/settings.yaml, then commit and push.
#   ./update.sh                      -> build, commit "Update CV and publications", push
#   ./update.sh "Add Nature paper"   -> same, with your own commit message
# Build only (no git):  python3 tools/build.py   (see docs/BUILD.md)
set -e
cd "$(dirname "$0")"
MESSAGE="${*:-Update CV and publications}"

python3 tools/build.py

git add -A
if git diff --cached --quiet; then
  echo "Nothing to commit"
else
  git commit -m "$MESSAGE" && git push origin main && echo "✓ Pushed"
fi
