# Purpose: 后端编译测试脚本：编译所有server代码，检查语法错误。

$ErrorActionPreference = "Stop"

Set-Location (Split-Path -Parent $PSScriptRoot)
$env:PYTHONPATH = (Get-Location).Path

python -m compileall server
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

python -m pytest server/tests -q
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
