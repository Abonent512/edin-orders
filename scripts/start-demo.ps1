$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Установите Python 3.12+' }
& .venv/Scripts/python.exe -m pip install -r backend/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Ошибка установки Python-зависимостей' }
if (-not (Test-Path frontend/dist)) {
    Push-Location frontend
    npm ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка npm ci' }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw 'Ошибка сборки' }
    Pop-Location
}
$env:DATA_DIR = (Join-Path (Get-Location) 'data')
$env:STATIC_DIR = (Join-Path (Get-Location) 'frontend/dist')
$env:PYTHONPATH = (Join-Path (Get-Location) 'backend')
$env:DEMO_MODE = 'true'
$env:COOKIE_SECURE = 'false'
$env:ALLOWED_ORIGINS = 'http://localhost:8000,http://127.0.0.1:8000'
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
& .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log
