#!/usr/bin/env bash
# Point git at the repo's tracked hooks. Run once after cloning.
set -euo pipefail
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true
echo "✓ hooks installed (core.hooksPath = .githooks)"
echo "  pre-commit : ruff, mypy --strict, no stray print(), fast tests, tool registration"
echo "  commit-msg : rejects empty / single-word / overlong subjects"
echo "  pre-push   : full test suite + smoke script"
