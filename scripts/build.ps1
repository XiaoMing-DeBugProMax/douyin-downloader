$ErrorActionPreference = 'Stop'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
$spec = Join-Path $projectRoot 'douyin_downloader.spec'
$exeName = (-join (0x6296, 0x97F3, 0x89C6, 0x9891, 0x4E0B, 0x8F7D | ForEach-Object {
    [char]$_
})) + '.exe'
$exe = Join-Path $projectRoot (Join-Path 'dist' $exeName)

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project virtual environment is missing: $python"
}

function Invoke-Python {
    param([string[]] $CommandArguments)

    & $python @CommandArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

Push-Location $projectRoot
try {
    Invoke-Python @('-m', 'pytest', '-q')
    Invoke-Python @('-m', 'ruff', 'check', 'src', 'tests', 'scripts')
    Invoke-Python @('-m', 'mypy', 'src')
    Invoke-Python @((Join-Path $PSScriptRoot 'check_sensitive.py'))
    Invoke-Python @((Join-Path $PSScriptRoot 'build_icon.py'))
    Invoke-Python @('-m', 'PyInstaller', '--clean', '--noconfirm', $spec)

    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) {
        throw "PyInstaller did not create the expected executable."
    }
    Invoke-Python @((Join-Path $PSScriptRoot 'check_sensitive.py'), '--artifact', $exe)
    Get-FileHash -LiteralPath $exe -Algorithm SHA256
}
finally {
    Pop-Location
}
