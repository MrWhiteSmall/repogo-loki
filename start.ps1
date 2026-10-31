$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
py -3 run.py @args
exit $LASTEXITCODE
