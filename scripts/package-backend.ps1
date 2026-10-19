$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$packagePath = Join-Path $projectRoot 'backend\.package'
New-Item -ItemType Directory -Force -Path $packagePath | Out-Null
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'backend\requirements-lambda.lock'))) {
    & (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $projectRoot 'scripts\lock-lambda-dependencies.py')
    if ($LASTEXITCODE -ne 0) { throw 'Linux dependency lock generation failed.' }
}
& (Join-Path $projectRoot '.venv\Scripts\python.exe') -m pip install -r (Join-Path $projectRoot 'backend\requirements-lambda.lock') --no-deps --platform manylinux_2_34_x86_64 --platform manylinux2014_x86_64 --implementation cp --python-version 3.12 --only-binary=:all: --target $packagePath --upgrade
if ($LASTEXITCODE -ne 0) { throw 'Lambda dependency packaging failed.' }
& (Join-Path $projectRoot '.venv\Scripts\python.exe') (Join-Path $projectRoot 'scripts\package-lambda.py')
if ($LASTEXITCODE -ne 0) { throw 'Lambda archive creation failed.' }
Write-Output 'Packaged Python 3.12 x86_64 Lambda dependencies and backend source.'
