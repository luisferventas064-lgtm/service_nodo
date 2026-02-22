param(
  [int]$MaxSilenceHours = 18,     # If daily did not update within <= 18h => ERROR
  [int]$CooldownMinutes = 60,     # Anti-spam
  [switch]$NoEmail,
  [switch]$TestRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\kpi_lib.ps1"

# Root = repo folder (assuming scripts/)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root      = Split-Path -Parent $ScriptDir

$LogsDir    = Join-Path $Root "logs"
$AlertsDir  = Join-Path $Root "alerts"
$SecretsDir = Join-Path $Root "secrets"
$ReportsDir = Join-Path $Root "reports"

$LogPath   = Join-Path $LogsDir "kpi_watcher_latest.log"
$DailyLog  = Join-Path $LogsDir "kpi_daily_latest.log"
$StatusJson = Join-Path $ReportsDir "kpi_status_latest.json"
$StatusMd   = Join-Path $ReportsDir "kpi_status_latest.md"

# --- PASO 23: Single-instance lock (anti overlap) -----------------------------
$LockPath = Join-Path $Root "locks\kpi_watcher.lock"
New-Item -ItemType Directory -Path (Split-Path $LockPath -Parent) -Force | Out-Null

$LockHandle = $null
try {
  # OpenOrCreate + exclusive lock; if busy => another watcher is running
  $LockHandle = [System.IO.File]::Open(
    $LockPath,
    [System.IO.FileMode]::OpenOrCreate,
    [System.IO.FileAccess]::ReadWrite,
    [System.IO.FileShare]::None
  )

  # Optional: write a small heartbeat into lock file (PID + timestamp)
  $LockHandle.SetLength(0)
  $writer = New-Object System.IO.StreamWriter($LockHandle, [System.Text.Encoding]::UTF8, 1024, $true)
  $writer.WriteLine("pid=$PID")
  $writer.WriteLine("ts=" + (Get-Date).ToString("s"))
  $writer.Flush()
  $writer.Dispose()
}
catch {
  # Another instance holds the lock => exit silently (no email / no alerts)
  try { Write-KpiEvent -EntryType Information -EventId 2301 -Message "Watcher overlap detected. Exiting. lock=$LockPath" } catch {}
  exit 0
}

$AlertsDirMode = $AlertsDir
$EvidenceDir = Join-Path $Root "evidence"
$EvidenceDirMode = $EvidenceDir
$AlertJson    = Join-Path $AlertsDirMode "kpi_alert_latest.json"
$HistoryNd    = Join-Path $AlertsDirMode "kpi_alert_history.ndjson"
$CooldownFile = Join-Path $AlertsDirMode "watcher_last_sent.txt"
$FingerprintFile = Join-Path $AlertsDirMode "watcher_last_fingerprint.txt"

$EV_OK_REAL   = 9010
$EV_ERR_REAL  = 9011
$EV_OK_TEST   = 12410
$EV_ERR_TEST  = 12411

function Log([string]$msg) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  New-Item -ItemType Directory -Force -Path $LogsDir | Out-Null
  Add-Content -Path $LogPath -Value "[$ts] $msg"
}

function Invoke-KpiRetentionSafe {
  try {
    Invoke-KpiRetention -Root $Root | Out-Null
  } catch {
    Log ("RETENTION_FAILED: " + $_.Exception.Message)
  }
}

function Get-LastEventSummary {
  param([int]$MaxEvents = 20)

  try {
    $ev = Get-WinEvent -FilterHashtable @{ LogName = 'Application'; ProviderName = 'NODO-KPI' } -MaxEvents $MaxEvents
    if (-not $ev) { return $null }

    $last = $ev | Select-Object -First 1
    $msg = [string]$last.Message
    $maxLen = [Math]::Min(240, $msg.Length)
    return [pscustomobject]@{
      time  = $last.TimeCreated.ToString("o")
      id    = $last.Id
      level = $last.LevelDisplayName
      msg   = $msg.Substring(0, $maxLen)
    }
  } catch {
    return $null
  }
}

function Write-KpiStatusDashboard {
  param(
    [string]$OverallStatus,   # OK / ERROR
    [string[]]$Errors,
    [string]$WatcherLog,
    [string]$DailyLog,
    [string]$WeeklyLog,
    [string]$AlertJsonPath,
    [string]$EvidenceZip = "",
    [string]$RunMode = "",
    [string]$RunId = "",
    [string]$RunHost = "",
    [string]$RunUser = ""
  )

  try {
    New-Item -ItemType Directory -Force -Path $ReportsDir | Out-Null

    $now = Get-Date
    $dailyAgeH = $null
    $weeklyAgeH = $null
    $watcherAgeH = $null

    if (Test-Path $DailyLog) { $dailyAgeH = (($now) - (Get-Item $DailyLog).LastWriteTime).TotalHours }
    if (Test-Path $WeeklyLog) { $weeklyAgeH = (($now) - (Get-Item $WeeklyLog).LastWriteTime).TotalHours }
    if (Test-Path $WatcherLog) { $watcherAgeH = (($now) - (Get-Item $WatcherLog).LastWriteTime).TotalHours }

    $lastEvent = Get-LastEventSummary -MaxEvents 25

    $obj = [pscustomobject]@{
      ts = $now.ToString("o")
      overall_status = $OverallStatus
      errors = $Errors
      run_mode = $RunMode
      run_id = $RunId
      run_host = $RunHost
      run_user = $RunUser

      paths = [pscustomobject]@{
        watcher_log = $WatcherLog
        daily_log   = $DailyLog
        weekly_log  = $WeeklyLog
        alert_json  = $AlertJsonPath
        evidence_zip = $EvidenceZip
        status_json = $StatusJson
        status_md   = $StatusMd
      }

      freshness_hours = [pscustomobject]@{
        watcher = $(if ($watcherAgeH -ne $null) { [Math]::Round($watcherAgeH, 2) } else { $null })
        daily   = $(if ($dailyAgeH -ne $null) { [Math]::Round($dailyAgeH, 2) } else { $null })
        weekly  = $(if ($weeklyAgeH -ne $null) { [Math]::Round($weeklyAgeH, 2) } else { $null })
      }

      last_event = $lastEvent
    }

    ($obj | ConvertTo-Json -Depth 6) | Set-Content -Path $StatusJson -Encoding UTF8

    # Minimal human-friendly markdown
    $md = @()
    $md += "# NODO KPI - STATUS (LATEST)"
    $md += "- **Timestamp:** $($obj.ts)"
    $md += "- **Overall:** **$($obj.overall_status)**"
    $md += "- **Run Mode:** $($obj.run_mode)"
    $md += "- **Run Id:** $($obj.run_id)"
    $md += "- **Run Host:** $($obj.run_host)"
    $md += "- **Run User:** $($obj.run_user)"
    if ($Errors -and $Errors.Count -gt 0) {
      $md += "- **Errors:**"
      foreach ($e in $Errors) { $md += "  - $e" }
    } else {
      $md += "- **Errors:** none"
    }
    $md += ""
    $md += "## Freshness (hours)"
    $md += "- watcher: $($obj.freshness_hours.watcher)"
    $md += "- daily: $($obj.freshness_hours.daily)"
    $md += "- weekly: $($obj.freshness_hours.weekly)"
    $md += ""
    $md += "## Paths"
    $md += "- watcher_log: $($obj.paths.watcher_log)"
    $md += "- daily_log: $($obj.paths.daily_log)"
    $md += "- weekly_log: $($obj.paths.weekly_log)"
    $md += "- alert_json: $($obj.paths.alert_json)"
    $md += "- evidence_zip: $($obj.paths.evidence_zip)"
    $md += ""
    if ($obj.last_event -ne $null) {
      $md += "## Last Event (NODO-KPI)"
      $md += "- time: $($obj.last_event.time)"
      $md += "- id: $($obj.last_event.id)"
      $md += "- level: $($obj.last_event.level)"
      $md += "- msg: $($obj.last_event.msg)"
    }

    ($md -join "`r`n") | Set-Content -Path $StatusMd -Encoding UTF8

    Log "STATUS_DASHBOARD_WRITTEN (json=$StatusJson md=$StatusMd)"
  } catch {
    Log ("STATUS_DASHBOARD_FAILED: " + $_.Exception.Message)
  }
}

function Import-DotEnv {
  param([string]$Path)
  if (-not (Test-Path $Path)) { throw "DotEnv not found: $Path" }
  Get-Content $Path | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $parts = $line.Split("=", 2)
    if ($parts.Count -ne 2) { return }
    $k = $parts[0].Trim()
    $v = $parts[1].Trim()
    if ($k -ne "") { [Environment]::SetEnvironmentVariable($k, $v, "Process") }
  }
  Log "DOTENV_LOADED ($Path)"
}

function Write-KpiEventLogInfo {
  param([Parameter(Mandatory=$true)][string]$Message, [int]$EventId = 9010)
  try { Write-EventLog -LogName Application -Source "NODO-KPI" -EventId $EventId -EntryType Information -Message $Message }
  catch { Log ("EVENTLOG_INFO_FAILED: " + $_.Exception.Message) }
}

function Write-KpiEventLogError {
  param([Parameter(Mandatory=$true)][string]$Message, [int]$EventId = 9011)
  try { Write-EventLog -LogName Application -Source "NODO-KPI" -EventId $EventId -EntryType Error -Message $Message }
  catch { Log ("EVENTLOG_ERROR_FAILED: " + $_.Exception.Message) }
}

function Read-JsonFileSafe {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return $null }
  try { return (Get-Content $Path -Raw | ConvertFrom-Json) }
  catch { Log ("JSON_READ_FAILED ($Path): " + $_.Exception.Message); return $null }
}

function Should-SendEmailNow {
  param([int]$CooldownMinutes, [string]$CooldownFile)
  try {
    if (-not (Test-Path $CooldownFile)) { return $true }
    $t = Get-Content $CooldownFile -Raw
    if ([string]::IsNullOrWhiteSpace($t)) { return $true }
    $last = [DateTime]::Parse($t)
    return ((Get-Date) - $last).TotalMinutes -ge $CooldownMinutes
  } catch {
    return $true
  }
}

function Mark-EmailSentNow {
  param([string]$CooldownFile)
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $CooldownFile) | Out-Null
  (Get-Date).ToString("o") | Set-Content -Path $CooldownFile
}

function Get-ErrorFingerprint {
  param([string[]]$Errors)
  # Normalize and sort so fingerprint remains stable
  $norm = $Errors | ForEach-Object { $_.Trim().ToUpperInvariant() } | Sort-Object
  $text = ($norm -join "|")

  $sha = [System.Security.Cryptography.SHA256]::Create()
  try {
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($text)
    $hash  = $sha.ComputeHash($bytes)
    return ([BitConverter]::ToString($hash) -replace "-", "").ToLowerInvariant()
  } finally {
    $sha.Dispose()
  }
}

function Get-LastFingerprint {
  param([string]$FingerprintFile)
  try {
    if (-not (Test-Path $FingerprintFile)) { return "" }
    return (Get-Content $FingerprintFile -Raw).Trim()
  } catch { return "" }
}

function Save-Fingerprint {
  param([string]$FingerprintFile, [string]$Fingerprint)
  try {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $FingerprintFile) | Out-Null
    Set-Content -Path $FingerprintFile -Value $Fingerprint
  } catch { }
}

function Get-TriageActions {
  param([string[]]$Errors)

  $actions = New-Object System.Collections.Generic.List[string]

  foreach ($e in $Errors) {
    $u = $e.ToUpperInvariant()

    if ($u -like "DAILY_LOG_MISSING*") {
      $actions.Add("DAILY_LOG_MISSING: Verifica que exista logs\kpi_daily_latest.log. Corre daily manual: powershell -File scripts\kpi_daily.ps1. Revisa Task Scheduler NODO_KPI_DAILY (Last Result) y permisos/ruta Start in.")
      continue
    }
    if ($u -like "DAILY_LOG_STALE*") {
      $actions.Add("DAILY_LOG_STALE: Daily no se actualiza. Revisa Task Scheduler NODO_KPI_DAILY (Last Run/Last Result). Revisa logs\kpi_daily_latest.log. Si el task no corre, revisa credenciales/Start in/lock file. Si corre pero falla, busca ERROR/EXCEPTION en el log.")
      continue
    }
    if ($u -like "ALERT_JSON_INDICATES_ERROR*") {
      $actions.Add("ALERT_JSON_ERROR: Abre alerts\kpi_alert_latest.json y alerts\kpi_alert_latest.txt. Identifica métrica (stuck/timeout/cancel). Confirma en DB (snapshots) y revisa kpi_daily_latest.log para el origen del error.")
      continue
    }
    if ($u -like "DOTENV*") {
      $actions.Add("DOTENV/SMTP: Confirma secrets\nodo_smtp.env existe y tiene NODO_SMTP_* correctos. Prueba weekly manual sin -NoEmail. Si 5.7.0 auth: App Password de Gmail (16 chars).")
      continue
    }
    if ($u -like "*EVENTLOG_*FAILED*") {
      $actions.Add("EVENTLOG_WRITE_FAILED: Ejecuta PowerShell con permisos adecuados o verifica que el Source 'NODO-KPI' exista. (Debe crearse 1 vez como Admin).")
      continue
    }
  }

  if ($actions.Count -eq 0) {
    $actions.Add("GENERAL: Ejecuta health: powershell -File scripts\kpi_health.ps1. Revisa EventLog NODO-KPI y logs (daily/weekly/watcher).")
  }

  return $actions
}

function Send-Email {
  param([string]$Subject, [string]$Body)

  $smtpHost = $env:NODO_SMTP_HOST
  $port = [int]($env:NODO_SMTP_PORT)
  $user = $env:NODO_SMTP_USER
  $pass = $env:NODO_SMTP_PASS
  $to   = $env:NODO_SMTP_TO
  $from = $env:NODO_SMTP_FROM

  if (-not $smtpHost -or -not $port -or -not $user -or -not $pass -or -not $to -or -not $from) {
    throw "Missing SMTP env vars (NODO_SMTP_*)"
  }

  $secure = ConvertTo-SecureString $pass -AsPlainText -Force
  $cred   = New-Object System.Management.Automation.PSCredential($user, $secure)

  Send-MailMessage -SmtpServer $smtpHost -Port $port -UseSsl `
    -Credential $cred -From $from -To $to -Subject $Subject -Body $Body
}

# Ensure lock is released even if script errors
try {
  # --- MAIN
  try {
  # --- PASO 24: Run identity ---------------------------------------------------
  $RunMode = if ($TestRun.IsPresent) { "TEST" } else { "REAL" }

  # Stable unique id per execution (UTC + PID)
  $RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ") + "-pid" + $PID

  $RunHost = $env:COMPUTERNAME
  $RunUser = $env:USERNAME

  # Event IDs: keep test isolated (offset)
  # Real: 2401.. ; Test: 12401..
  $EvBase = if ($RunMode -eq "TEST") { 12400 } else { 2400 }

  Write-KpiEvent -EntryType Information -EventId ($EvBase + 1) -Message "Watcher run start. mode=$RunMode run_id=$RunId host=$RunHost user=$RunUser"
  # --- PASO 25: TEST/REAL routing + run_* injection helpers --------------------
  $AlertsDirMode = if ($RunMode -eq "TEST") { Join-Path $AlertsDir "test" } else { $AlertsDir }
  New-Item -ItemType Directory -Path $AlertsDirMode -Force | Out-Null

  $EvidenceDirMode = if ($RunMode -eq "TEST") { Join-Path $EvidenceDir "test" } else { $EvidenceDir }
  New-Item -ItemType Directory -Path $EvidenceDirMode -Force | Out-Null

  $RunMeta = @{
    run_mode = $RunMode
    run_id   = $RunId
    run_host = $RunHost
    run_user = $RunUser
  }

  $AlertJson    = Join-Path $AlertsDirMode "kpi_alert_latest.json"
  $HistoryNd    = Join-Path $AlertsDirMode "kpi_alert_history.ndjson"
  $CooldownFile = Join-Path $AlertsDirMode "watcher_last_sent.txt"
  $FingerprintFile = Join-Path $AlertsDirMode "watcher_last_fingerprint.txt"
  # ---------------------------------------------------------------------------

  Log "START watcher MaxSilenceHours=$MaxSilenceHours CooldownMinutes=$CooldownMinutes NoEmail=$NoEmail TestRun=$TestRun AlertsDirMode=$AlertsDirMode EvidenceDirMode=$EvidenceDirMode"

  # Load SMTP env
  $envPath = Join-Path $SecretsDir "nodo_smtp.env"
  Import-DotEnv -Path $envPath

  $now = Get-Date
  $errors = @()

  # 1) Silence check (daily log freshness)
  if (-not (Test-Path $DailyLog)) {
    $errors += "DAILY_LOG_MISSING ($DailyLog)"
  } else {
    $ageH = (($now) - (Get-Item $DailyLog).LastWriteTime).TotalHours
    if ($ageH -gt $MaxSilenceHours) {
      $errors += ("DAILY_LOG_STALE ageHours={0:N1} max={1}" -f $ageH, $MaxSilenceHours)
    } else {
      Log ("DAILY_LOG_OK ageHours={0:N1}" -f $ageH)
    }
  }

  # 2) Alert JSON check (if exists)
  $aj = Read-JsonFileSafe -Path $AlertJson
  if ($aj -ne $null) {
    # Heuristic: detect "ERROR" on common fields without full schema dependency
    $raw = (Get-Content $AlertJson -Raw)
    if ($raw -match '"status"\s*:\s*"ERROR"' -or $raw -match '"level"\s*:\s*"ERROR"' -or $raw -match '"severity"\s*:\s*"HIGH"' -or $raw -match '"severity"\s*:\s*"CRITICAL"') {
      $errors += "ALERT_JSON_INDICATES_ERROR ($AlertJson)"
    } else {
      Log "ALERT_JSON_OK (no ERROR flags detected)"
    }
  } else {
    Log "ALERT_JSON_NOT_FOUND_OR_INVALID (ok)"
  }

  $OverallStatus = if ($errors.Count -eq 0) { "OK" } else { "ERROR" }

  # --- PASO 28: Noise control policy (WARN/ERROR) ------------------------------
  # Assumes:
  # - $OverallStatus in {OK,WARN,ERROR}
  # - $CooldownMinutes
  $policyNow = Get-Date

  # Minimal fingerprint if not already set
  $fpVar = Get-Variable -Name Fingerprint -ErrorAction SilentlyContinue
  $Fingerprint = if ($null -ne $fpVar) { [string]$fpVar.Value } else { "" }
  if ([string]::IsNullOrWhiteSpace([string]$Fingerprint)) {
    $errKey = ""
    try {
      if ($errors -and $errors.Count -gt 0) {
        $errKey = (($errors | ForEach-Object { [string]$_ }) -join "|")
      }
    } catch { }
    $Fingerprint = "$OverallStatus|$errKey"
  }

  # Persisted fingerprint file (mode-safe)
  $FpDir = Join-Path $Root "state"
  New-Item -ItemType Directory -Path $FpDir -Force | Out-Null
  $FpFile = if ($RunMode -eq "TEST") { Join-Path $FpDir "watcher_fingerprint_test.json" } else { Join-Path $FpDir "watcher_fingerprint_real.json" }

  $LastFp = $null
  if (Test-Path $FpFile) {
    try { $LastFp = Get-Content $FpFile -Raw | ConvertFrom-Json } catch { $LastFp = $null }
  }

  $LastFingerprint = $null
  $LastTs = $null
  if ($LastFp) {
    $LastFingerprint = $LastFp.fingerprint
    $LastTs = try { [datetime]$LastFp.ts } catch { $null }
  }

  $IsNewSignal = ($LastFingerprint -ne $Fingerprint)
  $CooldownOk  = $true
  if ($LastTs) {
    $minutes = ($policyNow - $LastTs).TotalMinutes
    $CooldownOk = ($minutes -ge $CooldownMinutes)
  }

  # Final policy knobs:
  $CollectOnWarn = $true           # set false if you only want evidence on ERROR
  $WarnNeedsNewSignal = $true      # WARN only if fingerprint changed
  $ErrorIgnoresCooldown = $true    # ERROR should always collect even in cooldown

  $ShouldCollect =
    (($OverallStatus -eq "ERROR") -and ($ErrorIgnoresCooldown -or $CooldownOk)) -or
    (($OverallStatus -eq "WARN")  -and $CollectOnWarn -and $CooldownOk -and ((-not $WarnNeedsNewSignal) -or $IsNewSignal))

  # If we are going to alert/collect, persist the new fingerprint timestamp
  if ($ShouldCollect) {
    $payload = @{
      ts = $policyNow.ToString("o")
      fingerprint = $Fingerprint
      run_id = $RunId
      run_mode = $RunMode
    }
    try { ($payload | ConvertTo-Json -Depth 4) | Set-Content -Path $FpFile -Encoding UTF8 } catch { }
  }

  Write-KpiEvent -EntryType Information -EventId ($EvBase + 9) -Message "NoisePolicy: status=$OverallStatus new=$IsNewSignal cooldown_ok=$CooldownOk should_collect=$ShouldCollect fp_file=$FpFile"
  # ---------------------------------------------------------------------------

  # --- PASO 27: Generate evidence pack on WARN/ERROR (mode-safe) ---------------
  $EvidenceZip = $null

  if ($ShouldCollect) {
    try {
      $EvidenceScript = Join-Path $ScriptDir "kpi_evidence_pack.ps1"

      $args = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $EvidenceScript,
        "-Root", $Root,
        "-OutDir", $EvidenceDirMode,
        "-RunMode", $RunMode,
        "-RunId", $RunId
      )

      if ($TestRun.IsPresent) { $args += "-TestRun" }

      # Call as child PowerShell to isolate errors and keep watcher stable
      $evidenceOut = & powershell.exe @args 2>$null
      $EvidenceZip = [string]($evidenceOut | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ -match '\.zip$' } | Select-Object -Last 1)

      if ($EvidenceZip) {
        Write-KpiEvent -EntryType Information -EventId ($EvBase + 6) -Message "Evidence linked. zip=$EvidenceZip"
      } else {
        Write-KpiEvent -EntryType Warning -EventId ($EvBase + 7) -Message "Evidence attempted but no zip returned."
      }
    }
    catch {
      Write-KpiEvent -EntryType Warning -EventId ($EvBase + 8) -Message ("Evidence failed: " + $_.Exception.Message)
    }
  }
  # ---------------------------------------------------------------------------

  if ($errors.Count -eq 0) {
    $WeeklyLog = Join-Path $Root "logs\kpi_weekly_latest.log"
    Write-KpiStatusDashboard -OverallStatus $OverallStatus -Errors @() -WatcherLog $LogPath -DailyLog $DailyLog -WeeklyLog $WeeklyLog -AlertJsonPath $AlertJson -EvidenceZip $EvidenceZip -RunMode $RunMeta.run_mode -RunId $RunMeta.run_id -RunHost $RunMeta.run_host -RunUser $RunMeta.run_user

    $msg = "WATCHER_OK | ts=$($now.ToString('yyyy-MM-dd HH:mm:ss')) | daily_log=$DailyLog | alert_json=$AlertJson | log=$LogPath"
    $eid = $(if ($TestRun) { $EV_OK_TEST } else { $EV_OK_REAL })
    $prefix = $(if ($TestRun) { "TEST: " } else { "" })
    Log "DONE_OK"
    Write-KpiEventLogInfo -Message ($prefix + $msg) -EventId $eid
    Invoke-KpiRetentionSafe
    exit 0
  }

  # Build alert payload
  $errText = ($errors -join " | ")
  $subject = "NODO KPI WATCHER ERROR"
  $body    = @"
WATCHER_ERROR
ts: $($now.ToString('yyyy-MM-dd HH:mm:ss'))
errors: $errText

paths:
  watcher_log: $LogPath
  daily_log:   $DailyLog
  alert_json:  $AlertJson
  alert_hist:  $HistoryNd
"@

  $triage = @(Get-TriageActions -Errors $errors)
  $body += "`r`n`r`n---`r`nTRIAGE (NEXT ACTIONS)`r`n"
  foreach ($a in $triage) { $body += "- $a`r`n" }
  Log ("TRIAGE_ACTIONS_BUILT count=" + $triage.Count)

  Log ("ERRORS: " + $errText)
  $WeeklyLog = Join-Path $Root "logs\kpi_weekly_latest.log"
  Write-KpiStatusDashboard -OverallStatus $OverallStatus -Errors $errors -WatcherLog $LogPath -DailyLog $DailyLog -WeeklyLog $WeeklyLog -AlertJsonPath $AlertJson -EvidenceZip $EvidenceZip -RunMode $RunMeta.run_mode -RunId $RunMeta.run_id -RunHost $RunMeta.run_host -RunUser $RunMeta.run_user

  # Append to ndjson history (optional)
  if (-not $TestRun) {
    try {
      New-Item -ItemType Directory -Force -Path $AlertsDirMode | Out-Null
      $obj = [pscustomobject]@{ ts = $now.ToString("o"); kind = "WATCHER_ERROR"; errors = $errors; watcher_log = $LogPath; daily_log = $DailyLog; alert_json = $AlertJson }
      ($obj | ConvertTo-Json -Compress) | Add-Content -Path $HistoryNd
    } catch {
      Log ("HISTORY_APPEND_FAILED: " + $_.Exception.Message)
    }
  } else {
    Log "TEST_RUN: history append skipped"
  }

  # EventLog
  $eid = $(if ($TestRun) { $EV_ERR_TEST } else { $EV_ERR_REAL })
  $prefix = $(if ($TestRun) { "TEST: " } else { "" })
  Write-KpiEventLogError -Message ($prefix + ("WATCHER_ERROR | ts=$($now.ToString('yyyy-MM-dd HH:mm:ss')) | " + $errText)) -EventId $eid

  # --- Smart cooldown: time-based cooldown, but if error fingerprint changed, send anyway
  $fingerprint = Get-ErrorFingerprint -Errors $errors
  $lastFp      = Get-LastFingerprint -FingerprintFile $FingerprintFile
  $fpChanged   = ($fingerprint -ne $lastFp)

  # --- Append dashboard summary to email (best effort)
  try {
    if (Test-Path $StatusJson) {
      $sj = Get-Content $StatusJson -Raw | ConvertFrom-Json
      $body += "`r`n`r`n---`r`nDASHBOARD SUMMARY`r`n"
      $body += "overall: $($sj.overall_status)`r`n"
      $body += "freshness_hours: daily=$($sj.freshness_hours.daily) weekly=$($sj.freshness_hours.weekly) watcher=$($sj.freshness_hours.watcher)`r`n"
      if ($sj.last_event -ne $null) {
        $body += "last_event: id=$($sj.last_event.id) level=$($sj.last_event.level) time=$($sj.last_event.time)`r`n"
      }
    }
  } catch {
    Log ("DASHBOARD_EMAIL_APPEND_FAILED: " + $_.Exception.Message)
  }

  if ($TestRun) {
    Log "TEST_RUN: email skipped"
  } elseif (-not $NoEmail) {
    $cooldownOk = Should-SendEmailNow -CooldownMinutes $CooldownMinutes -CooldownFile $CooldownFile

    if ($cooldownOk -or $fpChanged) {
      Send-Email -Subject $subject -Body $body
      Mark-EmailSentNow -CooldownFile $CooldownFile
      Save-Fingerprint -FingerprintFile $FingerprintFile -Fingerprint $fingerprint
      Log ("EMAIL_SENT" + ($(if ($fpChanged) { " (FINGERPRINT_CHANGED)" } else { "" })))
    } else {
      Log "EMAIL_SKIPPED_COOLDOWN (same fingerprint)"
    }
  } else {
    Log "EMAIL_SKIPPED_NoEmail"
  }

  Invoke-KpiRetentionSafe
  exit 1
  }
  catch {
  $msg = "WATCHER_FATAL: $($_.Exception.Message)"
  Log $msg
  $eid = $(if ($TestRun) { $EV_ERR_TEST } else { $EV_ERR_REAL })
  $prefix = $(if ($TestRun) { "TEST: " } else { "" })
  Write-KpiEventLogError -Message ($prefix + $msg) -EventId $eid
  Invoke-KpiRetentionSafe
  exit 1
  }
}
finally {
  if ($LockHandle) {
    try { $LockHandle.Dispose() } catch {}
  }
}
