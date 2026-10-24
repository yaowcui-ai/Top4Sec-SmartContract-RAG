$ErrorActionPreference = 'Stop'
$taskProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskProjectRoot
$taskPython = Join-Path $taskProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Project virtual environment is missing.' }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    $taskDockerDir = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'
    if (Test-Path -LiteralPath (Join-Path $taskDockerDir 'docker.exe')) { $env:PATH = $taskDockerDir + ';' + $env:PATH }
}
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
& $taskPython -X utf8 -m app.update_papers run --scheduled
exit $LASTEXITCODE
