# 装 scripts/git-hooks/ 下的所有 hook 到 .git/hooks/。
# clone 仓库后跑一次：powershell -ExecutionPolicy Bypass scripts/install-git-hooks.ps1
# Windows PowerShell 通用。

$ErrorActionPreference = "Stop"
$root = (git rev-parse --show-toplevel).Trim()
$src = Join-Path $root "scripts/git-hooks"
$dst = Join-Path $root ".git/hooks"

if (-not (Test-Path $dst)) { New-Item -ItemType Directory -Path $dst | Out-Null }

Get-ChildItem -File $src | ForEach-Object {
    $target = Join-Path $dst $_.Name
    Copy-Item -Force $_.FullName $target
    Write-Host "installed: $target"
}

Write-Host ""
Write-Host "完成。改 hook 后重新跑这个脚本。"
