param(
  [switch]$NoEmail,
  [int]$CooldownMinutes = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Resolve repo root (assuming scripts/)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir

$Watcher = Join-Path $ScriptDir "kpi_watcher.ps1"
$StateFp = Join-Path $Root "state\watcher_fingerprint_test.json"

function Run-Watcher {
  param(
    [string]$Name,
    [int]$MaxSilenceHours,
    [string]$ExpectedOverall,
    [int[]]$AllowedExitCodes = @(0)
  )

  Write-Host ""
  Write-Host "=== SMOKE: $Name (MaxSilenceHours=$MaxSilenceHours) ==="

  $args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $Watcher,
    "-TestRun",
    "-MaxSilenceHours", $MaxSilenceHours,
    "-CooldownMinutes", $CooldownMinutes
  )
  if ($NoEmail.IsPresent) { $args += "-NoEmail" }

  & powershell.exe @args
  if ($AllowedExitCodes -notcontains $LASTEXITCODE) {
    throw "Watcher returned exit code $LASTEXITCODE in $Name (allowed: $($AllowedExitCodes -join ','))"
  }

  $statusPath = Join-Path $Root "reports\kpi_status_latest.json"
  if (-not (Test-Path $statusPath)) { throw "Missing status json after ${Name}: $statusPath" }

  $obj = Get-Content $statusPath -Raw | ConvertFrom-Json
  Write-Host ("Status overall: " + $obj.overall_status + " | run_id=" + $obj.run_id)

  # Basic invariants
  if ($obj.run_mode -ne "TEST") { throw "run_mode is not TEST in $Name" }
  if (-not $obj.run_id) { throw "run_id missing in $Name" }
  if (-not $obj.paths.status_md) { throw "status_md path missing in $Name" }
  if ($ExpectedOverall -and $obj.overall_status -ne $ExpectedOverall) {
    throw "Unexpected overall in $Name. expected=$ExpectedOverall actual=$($obj.overall_status)"
  }

  return $obj
}

# Clean TEST fingerprint to make the first run "new signal"
if (Test-Path $StateFp) { Remove-Item -LiteralPath $StateFp -Force -ErrorAction SilentlyContinue }

# 1) OK scenario: large silence window => should_collect False (usually)
$obj1 = Run-Watcher -Name "OK" -MaxSilenceHours 18 -ExpectedOverall "OK" -AllowedExitCodes @(0)

# 2) ERROR scenario: force silence threshold 0 => should_collect True, evidence zip expected
# Reset fingerprint so ERROR is a "new signal"
if (Test-Path $StateFp) { Remove-Item -LiteralPath $StateFp -Force -ErrorAction SilentlyContinue }
$obj2 = Run-Watcher -Name "ERROR" -MaxSilenceHours 0 -ExpectedOverall "ERROR" -AllowedExitCodes @(1)

$zip2 = [string]$obj2.paths.evidence_zip
if (-not $zip2) { throw "Expected evidence_zip on ERROR but got empty" }
if (-not (Test-Path $zip2)) { throw "Evidence zip path not found: $zip2" }
Write-Host ("Evidence zip OK: " + $zip2)

# 3) WARN scenario (optional): if watcher currently returns only OK/ERROR,
# validate repeat error behavior inside cooldown with current noise policy.
$obj3 = Run-Watcher -Name "ERROR_REPEAT_WITHIN_COOLDOWN" -MaxSilenceHours 0 -ExpectedOverall "ERROR" -AllowedExitCodes @(1)

# --- PASO 30: Validate EventLog (TEST) ---------------------------------------
try {
  $Source = "NODO-KPI"

  # Expected TEST IDs (baseline)
  $ExpectedIds = @(
    12401,  # watcher run start (TEST)
    12409,  # NoisePolicy trace (TEST)
    12601,  # evidence start (TEST)
    12602,  # evidence done  (TEST)
    12406   # evidence linked (TEST) (watcher EvBase+6)
  )

  # Pull recent events (last 2 hours) for this source
  $since = (Get-Date).AddHours(-2)
  $events = Get-WinEvent -FilterHashtable @{
    LogName      = "Application"
    ProviderName = $Source
    StartTime    = $since
  } -ErrorAction Stop | Select-Object -First 200

  $found = @{}
  foreach ($id in $ExpectedIds) { $found[$id] = $false }

  foreach ($e in $events) {
    if ($found.ContainsKey($e.Id)) { $found[$e.Id] = $true }
  }

  $missing = @(
    $found.GetEnumerator() |
    Where-Object { -not $_.Value } |
    ForEach-Object { $_.Key } |
    Sort-Object
  )

  if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host ("[WARN] EventLog missing expected TEST IDs (last 2h): " + ($missing -join ", "))
    Write-Host "Tip: If you ran the smoke test earlier, expand the window (AddHours(-6))"
  } else {
    Write-Host ""
    Write-Host "[OK] EventLog validation passed (TEST IDs present)."
  }

  # Optional: print most recent 10 entries for quick triage
  Write-Host ""
  Write-Host "Recent NODO-KPI events (top 10):"
  $events | Select-Object -First 10 | ForEach-Object {
    $msg = $_.Message
    if ($msg -and $msg.Length -gt 140) { $msg = $msg.Substring(0, 140) + "..." }
    Write-Host ("- {0} | Id={1} | {2}" -f $_.TimeCreated.ToString("s"), $_.Id, $msg)
  }
}
catch {
  Write-Host ""
  Write-Host ("[WARN] EventLog validation skipped/failed: " + $_.Exception.Message)
  Write-Host "If running without admin rights or Source not registered, this can happen."
}
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "SMOKE TEST DONE."
Write-Host "Check folders: alerts\test , evidence\test , reports\kpi_status_latest.* , state\watcher_fingerprint_test.json"
