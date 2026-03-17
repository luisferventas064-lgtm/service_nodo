$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$logsDir = Join-Path $repoRoot "logs"
$lockDir = Join-Path $logsDir "tick_all.lock"
$logPath = Join-Path $logsDir "tick_all_latest.log"
$pythonExe = Join-Path $repoRoot ".venv\\Scripts\\python.exe"
$utf8 = [System.Text.UTF8Encoding]::new($false)

function Write-LogLine {
    param(
        [string]$Message,
        [switch]$Reset
    )

    $line = "$Message`r`n"
    if ($Reset) {
        [System.IO.File]::WriteAllText($logPath, $line, $utf8)
        return
    }
    [System.IO.File]::AppendAllText($logPath, $line, $utf8)
}

New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

try {
    New-Item -ItemType Directory -Path $lockDir -ErrorAction Stop | Out-Null
} catch {
    $timestamp = Get-Date -Format "s"
    Write-LogLine "[$timestamp] tick_all skipped: lock already present"
    exit 0
}

try {
    if (-not (Test-Path $pythonExe)) {
        throw "Python executable not found at $pythonExe"
    }

    Push-Location $repoRoot
    try {
        $timestamp = Get-Date -Format "s"
        Write-LogLine "[$timestamp] tick_all start" -Reset
        & $pythonExe manage.py tick_all 2>&1 | ForEach-Object {
            Write-LogLine "$_"
            $_
        }
        $exitCode = $LASTEXITCODE
        $timestamp = Get-Date -Format "s"
        Write-LogLine "[$timestamp] tick_all exit_code=$exitCode"
        exit $exitCode
    } finally {
        Pop-Location
    }
} finally {
    Remove-Item -Path $lockDir -Recurse -Force -ErrorAction SilentlyContinue
}
