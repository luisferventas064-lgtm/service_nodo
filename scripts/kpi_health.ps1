param(
  [int]$Tail = 30,
  [int]$Events = 5
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir

$StatusJson = Join-Path $Root "reports\kpi_status_latest.json"
$StatusMd   = Join-Path $Root "reports\kpi_status_latest.md"

$DailyLog   = Join-Path $Root "logs\kpi_daily_latest.log"
$WeeklyLog  = Join-Path $Root "logs\kpi_weekly_latest.log"
$WatcherLog = Join-Path $Root "logs\kpi_watcher_latest.log"

function Show-Header([string]$title) {
  Write-Host ""
  Write-Host ("=" * 78)
  Write-Host $title
  Write-Host ("=" * 78)
}

function TailFile([string]$path, [int]$n) {
  if (Test-Path $path) {
    Write-Host ("--- " + $path)
    Get-Content $path -Tail $n
  } else {
    Write-Host ("--- " + $path + " (MISSING)")
  }
}

Show-Header "NODO KPI HEALTH - $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# 1) Status (JSON)
Show-Header "STATUS (JSON)"
if (Test-Path $StatusJson) {
  try {
    $j = Get-Content $StatusJson -Raw | ConvertFrom-Json
    Write-Host ("overall_status : " + $j.overall_status)
    Write-Host ("timestamp      : " + $j.ts)
    Write-Host ("freshness_hours: daily={0} weekly={1} watcher={2}" -f $j.freshness_hours.daily, $j.freshness_hours.weekly, $j.freshness_hours.watcher)
    if ($j.errors -and $j.errors.Count -gt 0) {
      Write-Host "errors:"
      $j.errors | ForEach-Object { Write-Host ("  - " + $_) }
    } else {
      Write-Host "errors: none"
    }
    if ($j.last_event -ne $null) {
      Write-Host ("last_event     : id={0} level={1} time={2}" -f $j.last_event.id, $j.last_event.level, $j.last_event.time)
    }
    if ($j.paths -ne $null) {
      Write-Host ("paths.status_md: " + $j.paths.status_md)
    }
  } catch {
    Write-Host ("STATUS_JSON_PARSE_FAILED: " + $_.Exception.Message)
  }
} else {
  Write-Host "STATUS_JSON_MISSING"
}

# 2) Status (MD quick view)
Show-Header "STATUS (MD) - HEAD"
if (Test-Path $StatusMd) {
  Get-Content $StatusMd -TotalCount 20
} else {
  Write-Host "STATUS_MD_MISSING"
}

# 3) EventLog
Show-Header "EVENT LOG (NODO-KPI) - last $Events"
try {
  Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'NODO-KPI' } -MaxEvents $Events |
    Select-Object TimeCreated, Id, LevelDisplayName, Message |
    Format-Table -AutoSize
} catch {
  Write-Host ("EVENTLOG_READ_FAILED: " + $_.Exception.Message)
}

# 4) Logs tail
Show-Header "LOGS TAIL (last $Tail lines)"
TailFile -path $DailyLog -n $Tail
Write-Host ""
TailFile -path $WeeklyLog -n $Tail
Write-Host ""
TailFile -path $WatcherLog -n $Tail

Write-Host ""
Write-Host "DONE"
