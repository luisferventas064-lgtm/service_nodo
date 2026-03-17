param(
    [string]$TaskName = "NODO_TICK_ALL",
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$hiddenLauncher = (Resolve-Path (Join-Path $scriptDir "tick_all_hidden.vbs")).Path

$createArgs = @(
    "/Create",
    "/SC", "MINUTE",
    "/MO", "1",
    "/TN", $TaskName,
    "/TR", "`"wscript.exe`" `"$hiddenLauncher`"",
    "/F"
)

& schtasks @createArgs
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create scheduled task $TaskName"
}

& schtasks /Query /TN $TaskName /V /FO LIST
if ($LASTEXITCODE -ne 0) {
    throw "Failed to query scheduled task $TaskName after creation"
}

if ($RunNow) {
    & schtasks /Run /TN $TaskName
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start scheduled task $TaskName"
    }
}
