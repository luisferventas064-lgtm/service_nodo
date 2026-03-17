param(
    [string]$TaskName = "NODO_TICK_ALL"
)

$ErrorActionPreference = "Stop"

& schtasks /Delete /TN $TaskName /F
if ($LASTEXITCODE -ne 0) {
    throw "Failed to delete scheduled task $TaskName"
}
