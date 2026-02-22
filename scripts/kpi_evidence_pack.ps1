param(
  [string]$Root,
  [string]$OutDir,
  [ValidateSet("REAL","TEST")] [string]$RunMode = "REAL",
  [string]$RunId = "",
  [switch]$TestRun,
  [int]$DaysReports = 14,     # include weekly reports from last N days
  [int]$TailLines   = 400,    # lines to tail from latest logs
  [int]$MaxEvents   = 200     # NODO-KPI eventlog rows to export
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\kpi_lib.ps1"

# Resolve Root if not provided (assuming scripts/)
if (-not $Root) {
  $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $Root      = Split-Path -Parent $ScriptDir
}

$EvidenceDirDefault = Join-Path $Root "evidence"
if (-not $OutDir) { $OutDir = $EvidenceDirDefault }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

# Keep mode/id consistent when TestRun is used
if (-not $RunId) {
  $RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-pid" + $PID
}
if ($TestRun.IsPresent -and $RunMode -ne "TEST") { $RunMode = "TEST" }

$EvBase = if ($RunMode -eq "TEST") { 12600 } else { 2600 }
Write-KpiEvent -EntryType Information -EventId ($EvBase + 1) -Message "Evidence pack start. mode=$RunMode run_id=$RunId outdir=$OutDir"

$LogsDir   = Join-Path $Root "logs"
$AlertsDir = Join-Path $Root "alerts"
$ReportsDir= Join-Path $Root "reports"
$AlertsDirMode = if ($RunMode -eq "TEST") { Join-Path $AlertsDir "test" } else { $AlertsDir }

$PackDir = Join-Path $OutDir ("kpi_evidence_tmp_{0}_{1}" -f $RunMode, $RunId)
if (Test-Path $PackDir) { Remove-Item -LiteralPath $PackDir -Recurse -Force -ErrorAction SilentlyContinue }

function EnsureDir([string]$p){ New-Item -ItemType Directory -Force -Path $p | Out-Null }

function CopyIfExists([string]$src,[string]$dstDir){
  if (Test-Path $src) { Copy-Item $src -Destination $dstDir -Force }
}

function TailToFile([string]$src,[string]$dst,[int]$n){
  if (Test-Path $src) {
    Get-Content $src -Tail $n | Set-Content -Path $dst -Encoding UTF8
  }
}

EnsureDir $PackDir
EnsureDir (Join-Path $PackDir "logs")
EnsureDir (Join-Path $PackDir "alerts")
EnsureDir (Join-Path $PackDir "reports")
EnsureDir (Join-Path $PackDir "eventlog")

# 1) Logs (full copies of latest logs + tails)
CopyIfExists (Join-Path $LogsDir "kpi_daily_latest.log")   (Join-Path $PackDir "logs")
CopyIfExists (Join-Path $LogsDir "kpi_weekly_latest.log")  (Join-Path $PackDir "logs")
CopyIfExists (Join-Path $LogsDir "kpi_watcher_latest.log") (Join-Path $PackDir "logs")

TailToFile (Join-Path $LogsDir "kpi_daily_latest.log")   (Join-Path $PackDir "logs\kpi_daily_tail.txt")   $TailLines
TailToFile (Join-Path $LogsDir "kpi_weekly_latest.log")  (Join-Path $PackDir "logs\kpi_weekly_tail.txt")  $TailLines
TailToFile (Join-Path $LogsDir "kpi_watcher_latest.log") (Join-Path $PackDir "logs\kpi_watcher_tail.txt") $TailLines

# 2) Alerts (latest + history), routed by mode
CopyIfExists (Join-Path $AlertsDirMode "kpi_alert_latest.json")   (Join-Path $PackDir "alerts")
CopyIfExists (Join-Path $AlertsDirMode "kpi_alert_latest.txt")    (Join-Path $PackDir "alerts")
CopyIfExists (Join-Path $AlertsDirMode "kpi_alert_history.ndjson") (Join-Path $PackDir "alerts")
CopyIfExists (Join-Path $AlertsDirMode "watcher_last_sent.txt")    (Join-Path $PackDir "alerts")
CopyIfExists (Join-Path $AlertsDirMode "watcher_last_fingerprint.txt") (Join-Path $PackDir "alerts")

# 3) Dashboard latest
CopyIfExists (Join-Path $ReportsDir "kpi_status_latest.json") (Join-Path $PackDir "reports")
CopyIfExists (Join-Path $ReportsDir "kpi_status_latest.md")   (Join-Path $PackDir "reports")

# 4) Weekly reports (last N days)
if (Test-Path $ReportsDir) {
  $cutoff = (Get-Date).AddDays(-$DaysReports)
  Get-ChildItem $ReportsDir -File -Filter "kpi_weekly_*.md"   | Where-Object { $_.LastWriteTime -ge $cutoff } | Copy-Item -Destination (Join-Path $PackDir "reports") -Force
  Get-ChildItem $ReportsDir -File -Filter "kpi_weekly_*.json" | Where-Object { $_.LastWriteTime -ge $cutoff } | Copy-Item -Destination (Join-Path $PackDir "reports") -Force
}

# 5) EventLog dump (NODO-KPI)
try {
  $ev = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'NODO-KPI' } -MaxEvents $MaxEvents |
    Select-Object TimeCreated, Id, LevelDisplayName, Message

  $ev | Export-Csv -NoTypeInformation -Encoding UTF8 -Path (Join-Path $PackDir "eventlog\nodo_kpi_events.csv")
} catch {
  ("EVENTLOG_EXPORT_FAILED: " + $_.Exception.Message) | Set-Content -Path (Join-Path $PackDir "eventlog\eventlog_error.txt") -Encoding UTF8
}

# 6) Basic manifest
$manifest = @()
$manifest += "NODO KPI Evidence Pack"
$manifest += "timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
$manifest += "root: $Root"
$manifest += "run_mode: $RunMode"
$manifest += "run_id: $RunId"
$manifest += "included_reports_last_days: $DaysReports"
$manifest += "tail_lines: $TailLines"
$manifest += "max_events: $MaxEvents"
$manifest += ""
$manifest += "paths:"
$manifest += "  logs: $LogsDir"
$manifest += "  alerts: $AlertsDirMode"
$manifest += "  reports: $ReportsDir"
($manifest -join "`r`n") | Set-Content -Path (Join-Path $PackDir "MANIFEST.txt") -Encoding UTF8

# 7) OUTPUT ZIP (standardized)
$ZipName = "kpi_evidence_{0}_{1}.zip" -f $RunMode, $RunId
$ZipPath = Join-Path $OutDir $ZipName
if (Test-Path $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue }

$filesToPack = @(Get-ChildItem -Path $PackDir -File -Recurse -ErrorAction SilentlyContinue)
if ($filesToPack.Count -gt 0) {
  Compress-Archive -Path (Join-Path $PackDir "*") -DestinationPath $ZipPath -Force
} else {
  $tmp = Join-Path $OutDir ("evidence_empty_{0}.txt" -f $RunId)
  "No evidence files collected. mode=$RunMode run_id=$RunId ts=$(Get-Date -Format o)" | Set-Content -Path $tmp -Encoding UTF8
  Compress-Archive -Path $tmp -DestinationPath $ZipPath -Force
  Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
}

try {
  Invoke-KpiRetention -Root $Root | Out-Null
} catch {
  Write-Host ("RETENTION_FAILED: " + $_.Exception.Message)
}

Write-KpiEvent -EntryType Information -EventId ($EvBase + 2) -Message "Evidence pack done. zip=$ZipPath"
Write-Host "EVIDENCE_PACK_OK"
Write-Host "DIR: $PackDir"
Write-Host "ZIP: $ZipPath"
$ZipPath
