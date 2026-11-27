# Windows PowerShell 5.1. Pass --no-workers or --real-media as needed.
$ErrorActionPreference = 'Stop'
$launcher = Join-Path (Split-Path $PSScriptRoot -Parent) 'workers/dev_local.py'
foreach ($command in @('python', 'py')) {
    if (Get-Command $command -ErrorAction SilentlyContinue) {
        $prefix = @()
        if ($command -eq 'py') { $prefix = @('-3') }
        & $command @prefix -c 'import sys; sys.exit(sys.version_info < (3, 12))'
        if ($LASTEXITCODE -eq 0) {
            & $command @prefix $launcher @args
            exit $LASTEXITCODE
        }
    }
}
throw 'Install Python 3.12 or newer to launch Kinetiq Studio.'
