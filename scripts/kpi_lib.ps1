# Shared helpers for NODO KPI System

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-KpiEvent {
  param(
    [Parameter(Mandatory)][ValidateSet("Information","Warning","Error")] [string]$EntryType,
    [Parameter(Mandatory)][int]$EventId,
    [Parameter(Mandatory)][string]$Message,
    [string]$Source = "NODO-KPI"
  )

  try {
    # Source should already exist (real + test separated); if not, fallback silently.
    Write-EventLog -LogName Application -Source $Source -EntryType $EntryType -EventId $EventId -Message $Message
  } catch {
    # No-op: avoid breaking KPI pipeline because of EventLog permission/source issues
  }
}

function Remove-OldFiles {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string[]]$Include,
    [Parameter(Mandatory)][int]$MaxAgeDays
  )

  if (-not (Test-Path $Path)) { return @{ removed = 0; path = $Path } }

  $cutoff = (Get-Date).AddDays(-1 * [Math]::Abs($MaxAgeDays))
  $files = @(Get-ChildItem -Path $Path -File -Recurse -ErrorAction SilentlyContinue |
           Where-Object { $Include -contains $_.Extension.ToLower() -or $Include -contains $_.Name.ToLower() })

  $toRemove = $files | Where-Object { $_.LastWriteTime -lt $cutoff }

  $removed = 0
  foreach ($f in $toRemove) {
    try {
      Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop
      $removed++
    } catch { }
  }

  return @{ removed = $removed; path = $Path; cutoff = $cutoff }
}

function Keep-OnlyLatestN {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][string]$Filter,
    [Parameter(Mandatory)][int]$Keep
  )

  if (-not (Test-Path $Path)) { return @{ removed = 0; kept = 0; path = $Path } }

  $items = @(Get-ChildItem -Path $Path -File -Filter $Filter -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending)

  $kept = [Math]::Max(0, [Math]::Min($Keep, $items.Count))
  $toRemove = @()
  if ($items.Count -gt $Keep) {
    $toRemove = $items | Select-Object -Skip $Keep
  }

  $removed = 0
  foreach ($f in $toRemove) {
    try { Remove-Item -LiteralPath $f.FullName -Force -ErrorAction Stop; $removed++ } catch { }
  }

  return @{ removed = $removed; kept = $kept; path = $Path }
}

function Invoke-KpiRetention {
  param(
    [Parameter(Mandatory)][string]$Root,

    # Default policies (edit if you want)
    [int]$ReportsMaxAgeDays  = 21,   # keep historical reports 3 weeks
    [int]$EvidenceMaxAgeDays = 45,   # keep evidence packs 45 days
    [int]$LogsMaxAgeDays     = 14,   # keep raw logs 2 weeks
    [int]$KeepLatestStatusN  = 5     # keep last 5 status snapshots besides latest
  )

  $reportsPath  = Join-Path $Root "reports"
  $evidencePath = Join-Path $Root "evidence"
  $logsPath     = Join-Path $Root "logs"

  $summary = [ordered]@{
    ts = (Get-Date).ToString("s")
    root = $Root
    reports = $null
    evidence = $null
    logs = $null
    latest_trim = $null
  }

  # 1) Trim reports (md/json) older than policy
  $r1 = Remove-OldFiles -Path $reportsPath -Include @(".md",".json") -MaxAgeDays $ReportsMaxAgeDays

  # 2) Keep only N latest snapshots if you generate many kpi_status_YYYYMMDD_HHMM.json/md
  #    (does NOT touch kpi_status_latest.json/md)
  $r2json = Keep-OnlyLatestN -Path $reportsPath -Filter "kpi_status_*.json" -Keep $KeepLatestStatusN
  $r2md   = Keep-OnlyLatestN -Path $reportsPath -Filter "kpi_status_*.md"   -Keep $KeepLatestStatusN

  # 3) Trim evidence zips older than policy
  $e1 = Remove-OldFiles -Path $evidencePath -Include @(".zip") -MaxAgeDays $EvidenceMaxAgeDays

  # 4) Trim logs older than policy
  $l1 = Remove-OldFiles -Path $logsPath -Include @(".log",".txt",".json",".md") -MaxAgeDays $LogsMaxAgeDays

  $summary.reports = @{
    removed_old = $r1.removed
    removed_snapshots_json = $r2json.removed
    removed_snapshots_md   = $r2md.removed
    cutoff = $r1.cutoff
    path = $reportsPath
  }
  $summary.evidence = @{
    removed_old = $e1.removed
    cutoff = $e1.cutoff
    path = $evidencePath
  }
  $summary.logs = @{
    removed_old = $l1.removed
    cutoff = $l1.cutoff
    path = $logsPath
  }

  # 5) Emit event
  $msg = "KPI Retention done. reports_old=$($r1.removed) snapshots_json=$($r2json.removed) snapshots_md=$($r2md.removed) evidence_old=$($e1.removed) logs_old=$($l1.removed)"
  Write-KpiEvent -EntryType Information -EventId 2201 -Message $msg

  return $summary
}
