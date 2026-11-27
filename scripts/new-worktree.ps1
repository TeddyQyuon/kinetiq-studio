param(
  [Parameter(Mandatory = $true)]
  [string]$Branch,

  [string]$Base = "origin/main",

  [switch]$InstallDeps
)

$ErrorActionPreference = "Stop"
$env:Path = "C:\Program Files\nodejs;C:\AftergridToolchain\MinGit\cmd;C:\AftergridToolchain\bin;" + $env:Path

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$slug = ($Branch -replace "[^a-zA-Z0-9_-]", "-")
$worktreePath = Join-Path (Split-Path $repoRoot -Parent) "kinetiq-$slug"

Set-Location $repoRoot
git fetch origin main --quiet

if (Test-Path $worktreePath) {
  Write-Host "Worktree already exists at: $worktreePath" -ForegroundColor Yellow
} else {
  $branchExists = git branch --list $Branch
  if ($branchExists) {
    git worktree add $worktreePath $Branch
  } else {
    git worktree add $worktreePath -b $Branch $Base
  }
}

if ($InstallDeps) {
  Set-Location $worktreePath
  npm install
}

Write-Host ""
Write-Host "Ready! Open this isolated worktree in Antigravity:" -ForegroundColor Green
Write-Host "  $worktreePath"
