[CmdletBinding()]
param(
    [switch] $Check,

    [switch] $Repair,

    [string] $Python
)

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$verifyScript = Join-Path $PSScriptRoot 'verify.ps1'
$powerShell = Join-Path $PSHOME 'powershell.exe'

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
    & $powerShell `
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

$gitCommand = Get-Command git.exe -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $gitCommand) {
    Stop-Bootstrap -Code 'GIT_NOT_FOUND' -Message 'Git is required to locate the shared environment.'
}
$commonDirectoryValue = (& $gitCommand.Source -C $projectRoot rev-parse --git-common-dir 2>&1 |
    Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    Stop-Bootstrap -Code 'GIT_COMMON_DIR_FAILED' -Message $commonDirectoryValue
}
$commonDirectory = if ([System.IO.Path]::IsPathRooted($commonDirectoryValue)) {
    [System.IO.Path]::GetFullPath($commonDirectoryValue)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $commonDirectoryValue))
}
$environmentRoot = Split-Path -Parent $commonDirectory
$venvRoot = Join-Path $environmentRoot '.venv'
$venvPython = Join-Path $venvRoot 'Scripts\python.exe'

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
    if ($message -match 'Access is denied') {
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

$rebuildVenv = -not (Test-Path -LiteralPath $venvPython -PathType Leaf)
if (-not $rebuildVenv) {
    try {
        $venvProbe = (& $venvPython -c 'import sys; print(''.''.join(map(str, sys.version_info[:3])))' 2>&1 |
            Out-String).Trim()
        $venvProbeExitCode = $LASTEXITCODE
    }
    catch {
        $venvProbe = $_.Exception.Message
        $venvProbeExitCode = 1
    }
    if ($venvProbeExitCode -eq 0) {
        try {
            & $venvPython -m pip --version *> $null
            $venvProbeExitCode = $LASTEXITCODE
        }
        catch {
            $venvProbeExitCode = 1
        }
    }
    if ($venvProbeExitCode -ne 0 -or -not $venvProbe.StartsWith('3.12.')) {
        if (-not $Repair) {
            Stop-Bootstrap `
                -Code 'VENV_REPAIR_REQUIRED' `
                -Message "The existing environment is unusable. Re-run with -Repair: $venvRoot"
        }
        $rebuildVenv = $true
    }
}

if ($rebuildVenv) {
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

& $powerShell `
    -NoProfile `
    -ExecutionPolicy Bypass `
    -File $verifyScript `
    -Preflight
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Output 'BOOTSTRAP_RESULT=ready'
