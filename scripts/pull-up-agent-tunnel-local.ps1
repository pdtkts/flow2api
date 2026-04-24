# Like pull-up-agent-tunnel.ps1 but adds docker-compose.local-build.yml so
# flow2api:local and flow2api-agent-gateway:local are built from this repo (fresh UI + gateway).
# Run: .\scripts\pull-up-agent-tunnel-local.ps1

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Clear-Host
Write-Host "git pull" -ForegroundColor Cyan
git pull
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "docker compose (local build flow2api + agent-gateway + tunnel)" -ForegroundColor Cyan
docker compose `
  -f docker-compose.yml `
  -f docker-compose.agent-gateway.yml `
  -f docker-compose.tunnel.yml `
  -f docker-compose.agent-gateway.tunnel.yml `
  -f docker-compose.local-build.yml `
  up -d --build
exit $LASTEXITCODE
