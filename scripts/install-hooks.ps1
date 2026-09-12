# Point git at the repo's tracked hooks. Run once after cloning.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
git config core.hooksPath .githooks
Write-Output "hooks installed (core.hooksPath = .githooks)"
Write-Output "  pre-commit : ruff, mypy --strict, no stray print(), fast tests, tool registration"
Write-Output "  commit-msg : rejects empty / single-word / overlong subjects"
Write-Output "  pre-push   : full test suite + smoke script"
