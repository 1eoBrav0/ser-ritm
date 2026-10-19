$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 server.py
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python server.py
} else {
    $bundledPython = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
    if (Test-Path -LiteralPath $bundledPython) {
        & $bundledPython server.py
    } else {
        Write-Host 'Для запуска нужен Python 3.11 или новее: https://www.python.org/downloads/'
        exit 1
    }
}
