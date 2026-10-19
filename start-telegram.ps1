$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 run_telegram.py
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python run_telegram.py
} else {
    $ritmPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $ritmPython) {
        & $ritmPython run_telegram.py
    } else {
        Write-Host 'Для запуска нужен Python 3.11 или новее: https://www.python.org/downloads/'
        exit 1
    }
}
