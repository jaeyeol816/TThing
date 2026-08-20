Set-Location $PSScriptRoot
$env:PYTHONUTF8 = "1"

if (Test-Path .env) {
    Get-Content .env | ForEach-Object {
        if ($_ -match '^\s*([^#][^=]*?)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2])
        }
    }
}

Write-Host "[0/3] Killing process on port 8080..."
$pid8080 = (Get-NetTCPConnection -LocalPort 8080 -ErrorAction SilentlyContinue).OwningProcess
if ($pid8080) {
    Stop-Process -Id $pid8080 -Force -ErrorAction SilentlyContinue
    Write-Host "Killed PID $pid8080"
} else {
    Write-Host "Port 8080 is free"
}

Write-Host "[1/3] Installing dependencies..."
uv sync
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: uv sync"; pause; exit 1 }

Write-Host "[2/3] Preflight check..."
uv run python scripts/preflight.py
if ($LASTEXITCODE -ne 0) { Write-Host "FAILED: preflight"; pause; exit 1 }

Write-Host "[3/3] Starting server at http://127.0.0.1:8080"
uv run uvicorn --factory mesh.main:create_app --app-dir src --host 127.0.0.1 --port 8080
pause
