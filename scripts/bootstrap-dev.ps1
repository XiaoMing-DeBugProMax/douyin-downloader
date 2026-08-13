[CmdletBinding()]
param(
    [switch] $Check,

    [string] $Python
)

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$verifyScript = Join-Path $PSScriptRoot 'verify.ps1'
$venvRoot = Join-Path $projectRoot '.venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'

function Stop-Bootstrap {
    param(
        [string] $Code,
        [string] $Message,
        [int] $ExitCode = 1
    )

    [Console]::Error.WriteLine("BOOTSTRAP_ERROR=$Code")
    [Console]::Error.WriteLine("BOOTSTRAP_MESSAGE=$Message")
    exit $ExitCode
}

if ($Check) {
    Write-Output 'BOOTSTRAP_MODE=check'
    & powershell.exe `
        -NoProfile `
        -ExecutionPolicy Bypass `
        -File $verifyScript `
        -Preflight
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    Write-Output 'BOOTSTRAP_RESULT=ready'
    exit 0
}

$basePython = $Python
if ([string]::IsNullOrWhiteSpace($basePython)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notmatch '\\WindowsApps\\' } |
        Select-Object -First 1
    if ($null -ne $pythonCommand) {
        $basePython = $pythonCommand.Source
    }
}

if ([string]::IsNullOrWhiteSpace($basePython)) {
    Stop-Bootstrap `
        -Code 'PYTHON_NOT_FOUND' `
        -Message 'Python 3.12 was not found. Pass its full path with -Python.'
}
$basePython = [System.IO.Path]::GetFullPath($basePython)
if (-not (Test-Path -LiteralPath $basePython -PathType Leaf)) {
    Stop-Bootstrap `
        -Code 'PYTHON_NOT_FOUND' `
        -Message "Python executable does not exist: $basePython"
}

try {
    $baseVersion = (& $basePython -c 'import sys; print(''.''.join(map(str, sys.version_info[:3])))' 2>&1 |
        Out-String).Trim()
}
catch {
    $message = $_.Exception.Message
    if ($message -match 'Access is denied|拒绝访问') {
        Stop-Bootstrap `
            -Code 'PYTHON_EXECUTION_DENIED' `
            -Message "Python exists but cannot run in the current execution boundary: $basePython"
    }
    Stop-Bootstrap -Code 'PYTHON_UNAVAILABLE' -Message $message
}
if ($LASTEXITCODE -ne 0) {
    Stop-Bootstrap `
        -Code 'PYTHON_UNAVAILABLE' `
        -Message "Python exited with code ${LASTEXITCODE}: $baseVersion" `
        -ExitCode $LASTEXITCODE
}
if (-not $baseVersion.StartsWith('3.12.')) {
    Stop-Bootstrap `
        -Code 'PYTHON_VERSION_UNSUPPORTED' `
        -Message "Expected Python 3.12.x but found $baseVersion at $basePython"
}

Write-Output 'BOOTSTRAP_MODE=install'
Write-Output "BOOTSTRAP_BASE_PYTHON=$basePython"
Write-Output "BOOTSTRAP_PYTHON_VERSION=$baseVersion"
Write-Output "BOOTSTRAP_VENV=$venvRoot"

if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
    & $basePython -m venv --clear $venvRoot
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Push-Location $projectRoot
try {
    & $venvPython -m pip install -e '.[dev]'
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}

& powershell.exe `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File $verifyScript `
    -Preflight
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output 'BOOTSTRAP_RESULT=ready'
