# Run the SC2 Co-op overlay from repo root.
# Usage: .\run.ps1

$ErrorActionPreference = "Stop"

$env:PYTHONPATH = "$PSScriptRoot\src"
python -m overlay.app
