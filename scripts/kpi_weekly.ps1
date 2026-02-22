param(
  [switch]$NoEmail
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\kpi_lib.ps1"

# ---------- Paths ----------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path      # ...\scripts
$Root      = Split-Path -Parent $ScriptDir                        # project root
$LogsDir   = Join-Path $Root "logs"
$ReportsDir= Join-Path $Root "reports"
New-Item -ItemType Directory -Force -Path $LogsDir, $ReportsDir | Out-Null

$stamp = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile = Join-Path $LogsDir ("kpi_weekly_{0}.log" -f $stamp)
$LogLatest = Join-Path $LogsDir "kpi_weekly_latest.log"

function Write-Log([string]$msg) {
  $line = ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg)
  $line | Tee-Object -FilePath $LogFile -Append | Out-Host
  Copy-Item -Force $LogFile $LogLatest
}

function Invoke-KpiRetentionSafe {
  try {
    Invoke-KpiRetention -Root $Root | Out-Null
  } catch {
    Write-Log ("RETENTION_FAILED: " + $_.Exception.Message)
  }
}

function Import-DotEnv {
  param([string]$Path)

  if (-not (Test-Path $Path)) {
    Write-Log "DOTENV_NOT_FOUND: $Path"
    return
  }

  Get-Content $Path | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $parts = $line.Split("=", 2)
    if ($parts.Count -ne 2) { return }
    $k = $parts[0].Trim()
    $v = $parts[1].Trim()
    if ($k -ne "") { [Environment]::SetEnvironmentVariable($k, $v, "Process") }
  }

  Write-Log "DOTENV_LOADED"
}

function Write-KpiEventLogInfo {
  param(
    [Parameter(Mandatory=$true)][string]$Message,
    [int]$EventId = 9000
  )
  try {
    Write-EventLog -LogName Application -Source "NODO-KPI" -EventId $EventId -EntryType Information -Message $Message
  } catch {
    # No reventar el script por EventLog
    Add-Content -Path (Join-Path $Root "logs\kpi_weekly_latest.log") -Value ("[{0}] EVENTLOG_INFO_FAILED: {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_.Exception.Message)
  }
}

# ---------- SMTP helper (minimal, same env vars as daily) ----------
function Send-WeeklyEmail {
  param(
    [string]$Subject,
    [string]$Body,
    [string[]]$Attachments = @()
  )

  if ($NoEmail) {
    Write-Log "EMAIL_SKIPPED: NoEmail switch enabled"
    return
  }

  $hostS = $env:NODO_SMTP_HOST
  $portS = [int]($env:NODO_SMTP_PORT)
  $user  = $env:NODO_SMTP_USER
  $to    = $env:NODO_SMTP_TO
  $from  = $env:NODO_SMTP_FROM
  $pass  = $env:NODO_SMTP_PASS

  if ([string]::IsNullOrWhiteSpace($hostS) -or
      [string]::IsNullOrWhiteSpace($to)    -or
      [string]::IsNullOrWhiteSpace($from)  -or
      [string]::IsNullOrWhiteSpace($user)  -or
      [string]::IsNullOrWhiteSpace($pass)) {
    Write-Log "EMAIL_SKIPPED: missing SMTP env vars"
    return
  }

  try {
    $secure = ConvertTo-SecureString $pass -AsPlainText -Force
    $cred   = New-Object System.Management.Automation.PSCredential($user, $secure)

    $mail = New-Object System.Net.Mail.MailMessage
    $mail.From = $from
    $mail.To.Add($to)
    $mail.Subject = $Subject
    $mail.Body = $Body
    $mail.IsBodyHtml = $false

    foreach ($a in $Attachments) {
      if (Test-Path $a) {
        $att = New-Object System.Net.Mail.Attachment($a)
        $mail.Attachments.Add($att) | Out-Null
      }
    }

    $smtp = New-Object System.Net.Mail.SmtpClient($hostS, $portS)
    $smtp.EnableSsl = $true
    $smtp.Credentials = $cred
    $smtp.Send($mail)

    $mail.Dispose()
    Write-Log "EMAIL_SENT"
  } catch {
    Write-Log ("EMAIL_FAILED: " + $_.Exception.Message)
  }
}

# ---------- Activate venv ----------
try {
  $activate = Join-Path $Root ".venv\Scripts\Activate.ps1"
  if (-not (Test-Path $activate)) { throw "VENV not found: $activate" }
  . $activate
  Write-Log "VENV_OK"
  Import-DotEnv -Path (Join-Path $Root "secrets\nodo_smtp.env")
} catch {
  Write-Log ("VENV_FAIL: " + $_.Exception.Message)
  exit 1
}

# ---------- Extract snapshots from DB (last 7 days) ----------
# Generates JSONL on stdout: {"created_at":"...","payload":{...}}
$py = @"
import os, json, sys
from datetime import timedelta
from django.utils import timezone

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
sys.path.insert(0, os.getcwd())

import django
django.setup()

from jobs.models import KpiSnapshot

since = timezone.now() - timedelta(days=7)
qs = (KpiSnapshot.objects
      .filter(created_at__gte=since)
      .order_by("created_at"))

for s in qs:
    try:
        raw = s.payload_json
        if isinstance(raw, str):
            payload = json.loads(raw)
        elif raw:
            payload = raw
        else:
            payload = {}
    except Exception:
        payload = {}
    out = {
        "created_at": s.created_at.isoformat(),
        "payload": payload
    }
    print(json.dumps(out, ensure_ascii=False))
"@

$tmpPy = Join-Path $env:TEMP ("nodo_kpi_weekly_extract_{0}.py" -f $stamp)
Set-Content -Path $tmpPy -Value $py -Encoding UTF8

Write-Log "EXTRACT_START (last 7 days)"

# ---------- Run python (force cwd = project root, capture stdout/stderr) ----------
$tmpOut = Join-Path $env:TEMP ("nodo_kpi_weekly_out_{0}.txt" -f $stamp)
$tmpErr = Join-Path $env:TEMP ("nodo_kpi_weekly_err_{0}.txt" -f $stamp)

$PythonExe = "python"  # if preferred, replace with (Join-Path $Root ".venv\Scripts\python.exe")

$oldEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"

try {
  Push-Location $Root

  & $PythonExe $tmpPy 1> $tmpOut 2> $tmpErr
  $pyExitCode = $LASTEXITCODE

} finally {
  Pop-Location
  $ErrorActionPreference = $oldEap
}

# Read outputs
$stdout = @()
$stderr = @()
if (Test-Path $tmpOut) { $stdout = Get-Content $tmpOut -ErrorAction SilentlyContinue }
if (Test-Path $tmpErr) { $stderr = Get-Content $tmpErr -ErrorAction SilentlyContinue }

Remove-Item -Force $tmpOut, $tmpErr -ErrorAction SilentlyContinue
Remove-Item -Force $tmpPy -ErrorAction SilentlyContinue

# stderr log (for diagnostics)
foreach ($ln in $stderr) { if ($ln) { Write-Log ("PY_ERR: " + $ln) } }

if ($pyExitCode -ne 0) {
  Write-Log ("EXTRACT_FAIL: python exit code=" + $pyExitCode)
  exit 1
}

# Keep compatibility with previous flow:
$jsonLines = $stdout

if ($jsonLines.Count -eq 0) {
  Write-Log "NO_SNAPSHOTS_FOUND (0 lines)"
}

# Separates errors vs json (if python printed something else)
$rows = @()
foreach ($ln in $jsonLines) {
  $t = [string]$ln
  if ($t.Trim().StartsWith("{")) {
    try { $rows += ($t | ConvertFrom-Json) } catch { Write-Log ("JSON_PARSE_WARN: " + $t) }
  } else {
    Write-Log ("PY_OUT: " + $t)
  }
}

Write-Log ("SNAPSHOTS_COUNT=" + $rows.Count)

# ---------- Compute metrics ----------
function Get-Num($obj, [string]$key) {
  if ($null -eq $obj) { return $null }
  if ($obj.PSObject.Properties.Name -contains $key) {
    $v = $obj.$key
    if ($null -eq $v -or $v -eq "") { return $null }
    try { return [double]$v } catch { return $null }
  }
  return $null
}

$total = $rows.Count
if ($total -eq 0) {
  $subject = "NODO KPI Weekly - NO DATA (last 7 days)"
  $body = @"
NODO KPI Weekly Report

No KpiSnapshot records found in the last 7 days.
Log: $LogLatest
"@
  Send-WeeklyEmail -Subject $subject -Body $body -Attachments @($LogLatest)
  Write-Log "DONE_NO_DATA"
  Invoke-KpiRetentionSafe
  exit 0
}

# Common payload fields (adjust names if your payload_json differs)
$sumTotalJobs = 0
$hasTotalJobs = 0
$sumStuck = 0
$hasStuck = 0
$sumTimeoutRate = 0
$hasTimeoutRate = 0
$sumCancelRate = 0
$hasCancelRate = 0

# Peak tracking
$maxStuck = -1
$maxStuckAt = ""
$maxTimeoutRate = -1
$maxTimeoutRateAt = ""
$maxCancelRate = -1
$maxCancelRateAt = ""

foreach ($r in $rows) {
  $p = $r.payload

  $tj = Get-Num $p "total_jobs"
  if ($tj -ne $null) { $sumTotalJobs += $tj; $hasTotalJobs++ }

  $st = Get-Num $p "stuck"
  if ($st -ne $null) {
    $sumStuck += $st; $hasStuck++
    if ($st -gt $maxStuck) { $maxStuck = $st; $maxStuckAt = $r.created_at }
  }

  $tr = Get-Num $p "timeout_rate"
  if ($tr -ne $null) {
    $sumTimeoutRate += $tr; $hasTimeoutRate++
    if ($tr -gt $maxTimeoutRate) { $maxTimeoutRate = $tr; $maxTimeoutRateAt = $r.created_at }
  }

  $cr = Get-Num $p "cancel_rate"
  if ($cr -ne $null) {
    $sumCancelRate += $cr; $hasCancelRate++
    if ($cr -gt $maxCancelRate) { $maxCancelRate = $cr; $maxCancelRateAt = $r.created_at }
  }
}

$avgTotalJobs   = if ($hasTotalJobs -gt 0) { [math]::Round($sumTotalJobs / $hasTotalJobs, 2) } else { $null }
$avgStuck       = if ($hasStuck -gt 0) { [math]::Round($sumStuck / $hasStuck, 2) } else { $null }
$avgTimeoutRate = if ($hasTimeoutRate -gt 0) { [math]::Round($sumTimeoutRate / $hasTimeoutRate, 4) } else { $null }
$avgCancelRate  = if ($hasCancelRate -gt 0) { [math]::Round($sumCancelRate / $hasCancelRate, 4) } else { $null }

$start = $rows[0].created_at
$end   = $rows[-1].created_at

# ---------- Build report files ----------
$reportDate = Get-Date -Format "yyyy-MM-dd"
$mdPath   = Join-Path $ReportsDir ("kpi_weekly_{0}.md" -f $reportDate)
$jsonPath = Join-Path $ReportsDir ("kpi_weekly_{0}.json" -f $reportDate)

$md = @"
# NODO KPI Weekly Report - $reportDate

Period: **$start** -> **$end**
Snapshots: **$total**

## Averages (from payload_json)
- avg_total_jobs: **$avgTotalJobs**
- avg_stuck: **$avgStuck**
- avg_timeout_rate: **$avgTimeoutRate**
- avg_cancel_rate: **$avgCancelRate**

## Peaks
- max_stuck: **$maxStuck** at **$maxStuckAt**
- max_timeout_rate: **$maxTimeoutRate** at **$maxTimeoutRateAt**
- max_cancel_rate: **$maxCancelRate** at **$maxCancelRateAt**

## Files
- Log: $LogLatest
- JSON: $jsonPath
"@

Set-Content -Path $mdPath -Value $md -Encoding UTF8

$outObj = [ordered]@{
  report_date = $reportDate
  period_start = $start
  period_end = $end
  snapshots = $total
  averages = [ordered]@{
    avg_total_jobs = $avgTotalJobs
    avg_stuck = $avgStuck
    avg_timeout_rate = $avgTimeoutRate
    avg_cancel_rate = $avgCancelRate
  }
  peaks = [ordered]@{
    max_stuck = $maxStuck
    max_stuck_at = $maxStuckAt
    max_timeout_rate = $maxTimeoutRate
    max_timeout_rate_at = $maxTimeoutRateAt
    max_cancel_rate = $maxCancelRate
    max_cancel_rate_at = $maxCancelRateAt
  }
}
($outObj | ConvertTo-Json -Depth 10) | Set-Content -Path $jsonPath -Encoding UTF8

Write-Log ("REPORT_WRITTEN: " + $mdPath)
Write-Log ("REPORT_WRITTEN: " + $jsonPath)

# ---------- Email weekly report ----------
$subject = "NODO KPI Weekly - $reportDate (snapshots=$total)"
$body = @"
NODO KPI Weekly Report - $reportDate

Period: $start -> $end
Snapshots: $total

Averages:
- avg_total_jobs: $avgTotalJobs
- avg_stuck: $avgStuck
- avg_timeout_rate: $avgTimeoutRate
- avg_cancel_rate: $avgCancelRate

Peaks:
- max_stuck: $maxStuck at $maxStuckAt
- max_timeout_rate: $maxTimeoutRate at $maxTimeoutRateAt
- max_cancel_rate: $maxCancelRate at $maxCancelRateAt

Attachments:
- $mdPath
- $jsonPath
- $LogLatest
"@

Send-WeeklyEmail -Subject $subject -Body $body -Attachments @($mdPath, $jsonPath, $LogLatest)

Write-Log "DONE_OK"

# --- Retention: keep weekly reports for 84 days (12 weeks)
try {
  $ReportsDir = Join-Path $Root "reports"
  if (Test-Path $ReportsDir) {
    $cutoff = (Get-Date).AddDays(-84)
    Get-ChildItem $ReportsDir -File -Filter "kpi_weekly_*.md"  | Where-Object { $_.LastWriteTime -lt $cutoff } | Remove-Item -Force -ErrorAction Stop
    Get-ChildItem $ReportsDir -File -Filter "kpi_weekly_*.json" | Where-Object { $_.LastWriteTime -lt $cutoff } | Remove-Item -Force -ErrorAction Stop
    Add-Content -Path (Join-Path $Root "logs\kpi_weekly_latest.log") -Value ("[{0}] REPORTS_RETENTION_OK (cutoff={1})" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $cutoff.ToString("yyyy-MM-dd"))
  }
} catch {
  Add-Content -Path (Join-Path $Root "logs\kpi_weekly_latest.log") -Value ("[{0}] REPORTS_RETENTION_FAILED: {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_.Exception.Message)
}

$LogPath = $LogLatest
$ReportMdPath = $mdPath
$ReportJsonPath = $jsonPath
$okMsg = "WEEKLY_OK | ts=$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) | log=$($LogPath) | report_md=$($ReportMdPath) | report_json=$($ReportJsonPath)"
Write-KpiEventLogInfo -Message $okMsg -EventId 9000

Invoke-KpiRetentionSafe

exit 0
