param(
  [Parameter(Mandatory = $true)]
  [string]$Branch,

  [switch]$DeleteBranch
)

$ErrorActionPreference = "Stop"
$env:Path = "C:\Program Files\nodejs;C:\AftergridToolchain\MinGit\cmd;C:\AftergridToolchain\bin;" + $env:Path

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$slug = ($Branch -replace "[^a-zA-Z0-9_-]", "-")
$worktreePath = Join-Path (Split-Path $repoRoot -Parent) "kinetiq-$slug"

Set-Location $repoRoot
if (Test-Path $worktreePath) {
  git worktree remove $worktreePath --force
}
git worktree prune

if ($DeleteBranch) {
  git branch -D $Branch
}

Write-Host "Cleaned up worktree for branch: $Branch" -ForegroundColor Green
