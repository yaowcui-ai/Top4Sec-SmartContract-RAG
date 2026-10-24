$ErrorActionPreference = 'Stop'
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskProjectRoot
$taskImage = 'ghcr.io/hkuds/lightrag@sha256:5bdbd524931b011df246fe20888d110cef691e6804c12cde636a2b746d7de27e'
docker compose --profile full stop app lightrag
if ($LASTEXITCODE -ne 0) { throw 'Could not stop this project services.' }
Write-Host 'Ensure any PowerShell uvicorn for port 8003 is stopped. Building this project index only.'
docker run --rm --name scpaper-index-build -v "${taskProjectRoot}:/project" --entrypoint /app/.venv/bin/python $taskImage /project/scripts/build_index.py --execute
if ($LASTEXITCODE -ne 0) { throw 'Build incomplete. Inspect receipts; do not claim the index is ready.' }
