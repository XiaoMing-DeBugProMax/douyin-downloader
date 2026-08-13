[CmdletBinding(DefaultParameterSetName = 'Preflight')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Preflight')]
    [switch] $Preflight,

    [Parameter(Mandatory = $true, ParameterSetName = 'Focused')]
    [string] $Focused,

    [Parameter(Mandatory = $true, ParameterSetName = 'Full')]
    [switch] $Full
)

$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$sourceRoot = (Resolve-Path (Join-Path $projectRoot 'src')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'

function Stop-Verification {
    param(
        [string] $Code,
        [string] $Message,
        [int] $ExitCode = 1
    )

    [Console]::Error.WriteLine("VERIFY_ERROR=$Code")
    [Console]::Error.WriteLine("VERIFY_MESSAGE=$Message")
    exit $ExitCode
}

function Invoke-Python {
    param([string[]] $CommandArguments)

    & $python @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    Stop-Verification `
        -Code 'VENV_MISSING' `
        -Message "Project virtual environment is missing or incomplete: $python"
}

try {
    $version = (& $python -c 'import sys; print(''.''.join(map(str, sys.version_info[:3])))' 2>&1 |
        Out-String).Trim()
}
catch {
    $message = $_.Exception.Message
    if ($message -match 'Access is denied|拒绝访问|Unable to create process') {
        Stop-Verification `
            -Code 'PYTHON_EXECUTION_DENIED' `
            -Message "Python exists but cannot run in the current execution boundary: $python"
    }
    Stop-Verification -Code 'PYTHON_UNAVAILABLE' -Message $message
}

if ($LASTEXITCODE -ne 0) {
    Stop-Verification `
        -Code 'PYTHON_UNAVAILABLE' `
        -Message "Python exited with code ${LASTEXITCODE}: $version" `
        -ExitCode $LASTEXITCODE
}
if (-not $version.StartsWith('3.12.')) {
    Stop-Verification `
        -Code 'PYTHON_VERSION_UNSUPPORTED' `
        -Message "Expected Python 3.12.x but found $version at $python"
}

$env:PYTHONPATH = $sourceRoot
$probeCode = @'
import importlib.util
import json
from pathlib import Path

required = ('pytest', 'playwright', 'PyInstaller', 'ruff', 'mypy')
missing = [name for name in required if importlib.util.find_spec(name) is None]
import douyin_downloader

print('VERIFY_PROBE=' + json.dumps({
    'missing': missing,
    'module': str(Path(douyin_downloader.__file__).resolve()),
}))
'@

try {
    $probeOutput = @(& $python -c $probeCode 2>&1)
}
catch {
    Stop-Verification -Code 'DEPENDENCY_PROBE_FAILED' -Message $_.Exception.Message
}
if ($LASTEXITCODE -ne 0) {
    Stop-Verification `
        -Code 'DEPENDENCY_PROBE_FAILED' `
        -Message (($probeOutput | Out-String).Trim()) `
        -ExitCode $LASTEXITCODE
}

$probeLine = $probeOutput | Where-Object { $_ -is [string] -and $_.StartsWith('VERIFY_PROBE=') } |
    Select-Object -Last 1
if ($null -eq $probeLine) {
    Stop-Verification `
        -Code 'DEPENDENCY_PROBE_FAILED' `
        -Message (($probeOutput | Out-String).Trim())
}
$probe = $probeLine.Substring('VERIFY_PROBE='.Length) | ConvertFrom-Json
if ($probe.missing.Count -gt 0) {
    Stop-Verification `
        -Code 'DEPENDENCY_MISSING' `
        -Message ("Missing development dependencies: " + ($probe.missing -join ', '))
}

$modulePath = [System.IO.Path]::GetFullPath([string] $probe.module)
$sourcePrefix = $sourceRoot.TrimEnd('\') + '\'
if (-not $modulePath.StartsWith($sourcePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    Stop-Verification `
        -Code 'WRONG_SOURCE_IMPORT' `
        -Message "Expected imports below $sourceRoot but loaded $modulePath"
}

Write-Output "VERIFY_PROJECT_ROOT=$projectRoot"
Write-Output "VERIFY_PYTHON=$python"
Write-Output "VERIFY_PYTHON_VERSION=$version"
Write-Output "VERIFY_SOURCE_ROOT=$sourceRoot"
Write-Output "VERIFY_MODULE=$modulePath"
Write-Output 'VERIFY_DEPENDENCIES=ok'
Write-Output 'VERIFY_PREFLIGHT=ok'

if ($Preflight) {
    exit 0
}

if ($PSCmdlet.ParameterSetName -eq 'Focused') {
    $targetValue = $Focused.Split('::', 2)[0]
    if ([string]::IsNullOrWhiteSpace($targetValue)) {
        Stop-Verification -Code 'FOCUSED_TARGET_INVALID' -Message 'Focused pytest target is empty.'
    }
    $targetPath = if ([System.IO.Path]::IsPathRooted($targetValue)) {
        [System.IO.Path]::GetFullPath($targetValue)
    }
    else {
        [System.IO.Path]::GetFullPath((Join-Path $projectRoot $targetValue))
    }
    $testsRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot 'tests')).TrimEnd('\') + '\'
    if (-not $targetPath.StartsWith($testsRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Stop-Verification `
            -Code 'FOCUSED_TARGET_OUTSIDE_TESTS' `
            -Message "Focused pytest target must be below $testsRoot"
    }
    if (-not (Test-Path -LiteralPath $targetPath -PathType Leaf)) {
        Stop-Verification `
            -Code 'FOCUSED_TARGET_MISSING' `
            -Message "Focused pytest file does not exist: $targetPath"
    }

    Write-Output "VERIFY_FOCUSED=$Focused"
    Push-Location $projectRoot
    try {
        Invoke-Python @('-m', 'pytest', $Focused, '-q')
    }
    finally {
        Pop-Location
    }
    Write-Output 'VERIFY_RESULT=ok'
    exit 0
}

if ($Full) {
    Write-Output 'VERIFY_GATE=pytest'
    Push-Location $projectRoot
    try {
        Invoke-Python @('-m', 'pytest', '-q')
        Write-Output 'VERIFY_GATE=ruff'
        Invoke-Python @('-m', 'ruff', 'check', '.')
        Write-Output 'VERIFY_GATE=mypy'
        Invoke-Python @('-m', 'mypy', 'src')
    }
    finally {
        Pop-Location
    }
    Write-Output 'VERIFY_RESULT=ok'
    exit 0
}

Stop-Verification -Code 'MODE_NOT_IMPLEMENTED' -Message 'The selected verification mode is not implemented yet.'
