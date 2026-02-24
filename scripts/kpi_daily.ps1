<# 
scripts/kpi_daily.ps1
NODO KPI ENGINE + MONITORING
- Daily snapshot + cleanup + business alerts
- Writes logs + alerts/kpi_alert_latest.(txt|json)
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. "$PSScriptRoot\kpi_lib.ps1"

# ======================
# CONFIG (EDIT IF NEEDED)
# ======================
$WindowHours   = 168
$KeepDays      = 30
$LogsKeepDays  = 14
$DjangoSettings = "config.settings"   # <-- cambia si tu settings module es distinto

# ======================
# PATHS
# ======================
$Root     = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogsDir  = Join-Path $Root "logs"
$AlertsDir = Join-Path $Root "alerts"
$AlertHistory = Join-Path $AlertsDir "kpi_alert_history.ndjson"
$LockDir  = Join-Path $Root "locks"
$LockFile = Join-Path $LockDir "kpi_daily.lock"

New-Item -ItemType Directory -Force -Path $LogsDir   | Out-Null
New-Item -ItemType Directory -Force -Path $AlertsDir | Out-Null
New-Item -ItemType Directory -Force -Path $LockDir   | Out-Null

$ts = Get-Date -Format "yyyy-MM-dd_HH-mm-ss"
$LogFile       = Join-Path $LogsDir "kpi_daily_$ts.log"
$LogLatestFile = Join-Path $LogsDir "kpi_daily_latest.log"

# ======================
# LOGGING HELPERS
# ======================
function Write-Log {
  param([Parameter(Mandatory=$true)][string]$Message)
  $line = "{0} | {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
  $line | Tee-Object -FilePath $LogFile -Append | Out-Null
}

function Invoke-KpiRetentionSafe {
  try {
    Invoke-KpiRetention -Root $Root | Out-Null
  } catch {
    Write-Log ("RETENTION_FAILED: " + $_.Exception.Message)
  }
}

function Send-AlertEmail {
  param(
    [Parameter(Mandatory=$true)][string]$Subject,
    [Parameter(Mandatory=$true)][string]$Body
  )

  $hostSmtp = $env:NODO_SMTP_HOST
  $portSmtp = $env:NODO_SMTP_PORT
  $userSmtp = $env:NODO_SMTP_USER
  $passSmtp = $env:NODO_SMTP_PASS
  $toSmtp   = $env:NODO_SMTP_TO
  $fromSmtp = $env:NODO_SMTP_FROM

  if (-not $hostSmtp -or -not $portSmtp -or -not $userSmtp -or -not $passSmtp -or -not $toSmtp -or -not $fromSmtp) {
    Write-Log "EMAIL WARNING: SMTP env vars missing; skipping email."
    return
  }

  try {
    $secure = ConvertTo-SecureString $passSmtp -AsPlainText -Force
    $cred = New-Object System.Management.Automation.PSCredential($userSmtp, $secure)

    Send-MailMessage -SmtpServer $hostSmtp -Port ([int]$portSmtp) -UseSsl -Credential $cred `
      -To $toSmtp -From $fromSmtp -Subject $Subject -Body $Body

    Write-Log "EMAIL: sent error alert."
  }
  catch {
    Write-Log ("EMAIL WARNING: failed to send. " + $_.Exception.Message)
  }
}

function Write-KpiEventLogError {
  param(
    [string]$Message,
    [string]$LogPath,
    [string]$AlertJsonPath
  )

  $source  = "NODO-KPI"
  $logName = "Application"
  $eventId = 9001

  $full = @"
NODO KPI DAILY - ERROR

Message: $Message
Log: $LogPath
Alert JSON: $AlertJsonPath
Timestamp: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
"@

  try {
    # Si el source NO existe, esto puede fallar. Por eso se crea 1 vez como Admin.
    Write-EventLog -LogName $logName -Source $source -EventId $eventId -EntryType Error -Message $full
    "EVENTLOG_WRITTEN"
  } catch {
    # No rompemos la corrida por esto; ya tenemos logs + email.
    "EVENTLOG_FAILED: $($_.Exception.Message)"
  }
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
    Add-Content -Path (Join-Path $Root "logs\kpi_daily_latest.log") -Value ("[{0}] EVENTLOG_INFO_FAILED: {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_.Exception.Message)
  }
}

function Rotate-Logs {
  param([int]$KeepDays)
  $cutoff = (Get-Date).AddDays(-$KeepDays)
  Get-ChildItem -Path $LogsDir -File -Filter "kpi_daily_*.log" -ErrorAction SilentlyContinue |
    Where-Object { $_.LastWriteTime -lt $cutoff } |
    Remove-Item -Force -ErrorAction SilentlyContinue
}

function Release-Lock {
  try {
    if (Test-Path $LockFile) { Remove-Item $LockFile -Force -ErrorAction SilentlyContinue }
  } catch { }
}

# ======================
# PYTHON / VENV
# ======================
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
  throw "No se encontró python del venv en: $Py"
}

$ManagePy = Join-Path $Root "manage.py"
if (-not (Test-Path $ManagePy)) {
  throw "No se encontró manage.py en: $ManagePy"
}

# ======================
# EXEC HELPERS
# ======================
function Run-Manage {
  param([Parameter(Mandatory=$true)][string[]]$Args)

  $cmd = @($ManagePy) + $Args
  Write-Log ("RUN: python " + ($cmd -join " "))

  $pinfo = New-Object System.Diagnostics.ProcessStartInfo
  $pinfo.FileName = $Py
  $pinfo.WorkingDirectory = $Root.Path
  $pinfo.RedirectStandardOutput = $true
  $pinfo.RedirectStandardError  = $true
  $pinfo.UseShellExecute = $false
  $pinfo.Arguments = ($cmd | ForEach-Object { '"' + $_ + '"' }) -join " "

  $p = New-Object System.Diagnostics.Process
  $p.StartInfo = $pinfo
  [void]$p.Start()

  $stdout = $p.StandardOutput.ReadToEnd()
  $stderr = $p.StandardError.ReadToEnd()
  $p.WaitForExit()

  if ($stdout) { $stdout.TrimEnd() | ForEach-Object { Write-Log $_ } }
  if ($stderr) { $stderr.TrimEnd() | ForEach-Object { Write-Log ("STDERR: " + $_) } }

  if ($p.ExitCode -ne 0) {
    throw "manage.py falló con exit code $($p.ExitCode)"
  }

  return $stdout
}

function Get-LatestSnapshotPayloadJson {
  # Lee el ultimo snapshot desde DB y devuelve SOLO payload_json (string JSON).
  # Usamos manage.py shell -c para evitar problemas de PYTHONPATH/settings.
  $pycode = "import json; from jobs.models import KpiSnapshot; s=KpiSnapshot.objects.order_by('-created_at').first(); print(json.dumps({'__error__':'NO_SNAPSHOTS'}) if not s else s.payload_json)"
  $out = Run-Manage @("shell", "-c", $pycode)

  $lines = @((($out | Out-String) -split "`r?`n" | Where-Object { $_.Trim() -ne "" }))
  $lines = @($lines | Where-Object { $_ -notmatch "objects imported automatically" })
  if ($lines.Count -eq 0) {
    throw "Salida vacia leyendo KpiSnapshot.payload_json"
  }

  return $lines[-1].Trim()
}

# ==========================
# SINGLE-RUN LOCK (ANTI-PARALLEL)
# ==========================
if (Test-Path $LockFile) {
  try {
    $lockObj = Get-Content $LockFile -Raw | ConvertFrom-Json
    $lockTime = [datetime]$lockObj.timestamp
    $ageMins = ((Get-Date) - $lockTime).TotalMinutes

    # Considera "activo" si lock < 60 min (ajusta si tu run dura más)
    if ($ageMins -lt 60) {
      Write-Log ("LOCK: another run detected (age_minutes={0}). Exiting 0." -f [math]::Round($ageMins,2))
      exit 0
    }
    else {
      Write-Log ("LOCK: stale lock detected (age_minutes={0}). Taking over." -f [math]::Round($ageMins,2))
      Release-Lock
    }
  } catch {
    Write-Log "LOCK WARNING: lock exists but unreadable. Taking over."
    Release-Lock
  }
}

# Crear lock con info útil
$lockPayload = [ordered]@{
  timestamp = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
  pid       = $PID
  host      = $env:COMPUTERNAME
  user      = $env:USERNAME
}
($lockPayload | ConvertTo-Json -Depth 4) | Set-Content -Path $LockFile -Encoding UTF8

# Asegura liberar lock al salir (éxito o error)
try {
  Register-EngineEvent PowerShell.Exiting -Action {
    try { Remove-Item "$using:LockFile" -Force -ErrorAction SilentlyContinue } catch {}
  } | Out-Null
} catch {
  # No crítico
  Write-Log "LOCK WARNING: could not register exiting handler."
}

# ======================
# MAIN
# ======================
$hasError   = $false
$hasWarning = $false

# defaults para alert persistence (por si algo falla antes de leer snapshot)
$snapshotId   = $null
$rowsDeleted  = $null
$totalSnapshots = $null
$totalJobs    = $null
$stuck        = 0
$timeoutRate  = 0
$cancelRate   = 0

try {
  Write-Log "=== START KPI DAILY ==="
  Write-Log "ROOT=$($Root.Path)"
  Write-Log "window_hours=$WindowHours keep_days=$KeepDays logs_keep_days=$LogsKeepDays"

  # 1) Snapshot
  $saveOut = Run-Manage @("kpi_save_snapshot", "--hours", "$WindowHours")
  $saveText = ($saveOut | Out-String)
  if ($saveText -match "id=(\d+)") { $snapshotId = $Matches[1] }

  # 2) Cleanup
  $cleanupOut = Run-Manage @("kpi_cleanup_snapshots", "--keep-days", "$KeepDays")
  $cleanupText = ($cleanupOut | Out-String)
  if ($cleanupText -match "OK deleted\s+(\d+)\s+rows") { $rowsDeleted = $Matches[1] }

  # 3) List
  $listOut = Run-Manage @("kpi_list_snapshots")
  $listMatches = [regex]::Matches(($listOut | Out-String), "(?m)^id=\d+\b")
  $totalSnapshots = $listMatches.Count

  # 4) Read latest snapshot payload_json (no parsing of log lines -> evita encoding issues)
  $payloadJson = Get-LatestSnapshotPayloadJson

  # 5) Parse JSON in PowerShell
  $payload = $payloadJson | ConvertFrom-Json

  $hasNoSnapshots = $false
  if ($payload.PSObject.Properties.Name -contains "__error__") {
    if ($payload.__error__ -eq "NO_SNAPSHOTS") {
      $hasNoSnapshots = $true
    }
  }

  if ($hasNoSnapshots) {
    $hasError = $true
    Write-Log "ERROR: No hay snapshots en DB (KpiSnapshot vacío)."
  } else {
    # ===== Extract expected fields =====
    if (-not $snapshotId -and ($payload.PSObject.Properties.Name -contains "snapshot_id")) {
      $snapshotId = $payload.snapshot_id
    }
    if (-not $snapshotId) { $hasError = $true; Write-Log "ERROR: snapshot_id no disponible en payload_json." }

    if ($null -eq $rowsDeleted -and ($payload.PSObject.Properties.Name -contains "rows_deleted")) {
      $rowsDeleted = $payload.rows_deleted
    }
    if ($null -eq $rowsDeleted) { $hasWarning = $true; Write-Log "WARNING: rows_deleted no disponible en payload_json." }

    if (($null -eq $totalSnapshots -or [int]$totalSnapshots -eq 0) -and ($payload.PSObject.Properties.Name -contains "total_snapshots")) {
      $totalSnapshots = $payload.total_snapshots
    }
    if ($null -eq $totalSnapshots) { $hasWarning = $true; Write-Log "WARNING: total_snapshots no disponible en payload_json." }
    elseif ([int]$totalSnapshots -lt 3) { $hasWarning = $true; Write-Log "WARNING: total_snapshots < 3 (total_snapshots=$totalSnapshots)." }

    # Business metrics
    # Soporta 2 formatos: stuck_preview_len o stuck
    if ($payload.PSObject.Properties.Name -contains "stuck_preview_len") {
      $stuck = [int]($payload.stuck_preview_len)
    } elseif ($payload.PSObject.Properties.Name -contains "stuck_preview") {
      $preview = $payload.stuck_preview
      if ($null -eq $preview) { $stuck = 0 } else { $stuck = [int]$preview.Count }
    } elseif ($payload.PSObject.Properties.Name -contains "stuck") {
      $stuck = [int]($payload.stuck)
    } else {
      $stuck = 0
      $hasWarning = $true
      Write-Log "WARNING: Métrica stuck no disponible (stuck_preview_len/stuck)."
    }

    if ($payload.PSObject.Properties.Name -contains "timeout_rate") {
      $timeoutRate = [double]$payload.timeout_rate
    } elseif ($payload.PSObject.Properties.Name -contains "rates" -and $null -ne $payload.rates -and ($payload.rates.PSObject.Properties.Name -contains "timeout_rate")) {
      $timeoutRate = [double]$payload.rates.timeout_rate
    } elseif ($payload.PSObject.Properties.Name -contains "outcome_rates" -and $null -ne $payload.outcome_rates -and ($payload.outcome_rates.PSObject.Properties.Name -contains "expire_rate")) {
      $timeoutRate = [double]$payload.outcome_rates.expire_rate
    } else {
      $timeoutRate = 0
      $hasWarning = $true
      Write-Log "WARNING: timeout_rate no disponible en payload_json."
    }

    if ($payload.PSObject.Properties.Name -contains "cancel_rate") {
      $cancelRate = [double]$payload.cancel_rate
    } elseif ($payload.PSObject.Properties.Name -contains "rates" -and $null -ne $payload.rates -and ($payload.rates.PSObject.Properties.Name -contains "cancel_rate")) {
      $cancelRate = [double]$payload.rates.cancel_rate
    } elseif ($payload.PSObject.Properties.Name -contains "outcome_rates" -and $null -ne $payload.outcome_rates -and ($payload.outcome_rates.PSObject.Properties.Name -contains "cancel_rate")) {
      $cancelRate = [double]$payload.outcome_rates.cancel_rate
    } else {
      $cancelRate = 0
      $hasWarning = $true
      Write-Log "WARNING: cancel_rate no disponible en payload_json."
    }

    # ===== Business rules =====
    if ($stuck -gt 0) {
      $hasError = $true
      Write-Log "ERROR: stuck_preview_len > 0 (stuck=$stuck)"
    }

    # ==========================
    # DYNAMIC THRESHOLDS BY VOLUME
    # ==========================
    if ($payload.PSObject.Properties.Name -contains "total_jobs") {
      $totalJobs = [int]$payload.total_jobs
    } else {
      $hasWarning = $true
      Write-Log "WARNING: total_jobs no disponible en payload_json (no se aplicarán thresholds dinámicos)."
    }

    if ($null -ne $totalJobs) {
      # Siempre evaluar stuck (ya lo haces)
      # Evaluar rates SOLO si hay volumen suficiente
      if ($totalJobs -lt 20) {
        Write-Log "KPI VOLUME: total_jobs < 20 => rates ignored (stuck-only mode)"
      }
      elseif ($totalJobs -lt 100) {
        if ($timeoutRate -ge 0.30) { $hasWarning = $true; Write-Log "WARNING: timeout_rate >= 0.30 (low/med volume tier)" }
        if ($cancelRate  -ge 0.15) { $hasWarning = $true; Write-Log "WARNING: cancel_rate  >= 0.15 (low/med volume tier)" }
      }
      else {
        if ($timeoutRate -ge 0.20) { $hasWarning = $true; Write-Log "WARNING: timeout_rate >= 0.20 (high volume tier)" }
        if ($cancelRate  -ge 0.10) { $hasWarning = $true; Write-Log "WARNING: cancel_rate  >= 0.10 (high volume tier)" }
      }
    }
  }

  # ===== Structured output =====
  Write-Log ("KPI CHECK | window_hours={0} | total_jobs={1} | stuck={2} | timeout_rate={3} | cancel_rate={4}" -f $WindowHours, $totalJobs, $stuck, $timeoutRate, $cancelRate)

  # ===== Persist alerts (LATEST) =====
  $nowIso = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
  $alertTxt  = Join-Path $AlertsDir "kpi_alert_latest.txt"
  $alertJson = Join-Path $AlertsDir "kpi_alert_latest.json"

  $outPayload = [ordered]@{
    timestamp     = $nowIso
    severity      = "SUCCESS"
    snapshot_id   = $snapshotId
    window_hours  = $WindowHours
    total_jobs    = $totalJobs
    stuck         = $stuck
    timeout_rate  = $timeoutRate
    cancel_rate   = $cancelRate
    rows_deleted  = $rowsDeleted
    total_snapshots = $totalSnapshots
  }

  # ==========================
  # WEEKLY BASELINE (LAST 7 DAYS) FROM NDJSON
  # ==========================
  try {
    $historyPath = $AlertHistory
    $baselineDays = 7
    $cutoff = (Get-Date).AddDays(-$baselineDays)

    $baselineItems = @()

    if (Test-Path $historyPath) {
      $baselineItems = @(
        Get-Content -Path $historyPath -ErrorAction Stop |
        Where-Object { $_ -and $_.Trim().Length -gt 0 } |
        ForEach-Object {
          try { $_ | ConvertFrom-Json } catch { $null }
        } |
        Where-Object {
          $_ -ne $null -and
          $_.PSObject.Properties.Name -contains "timestamp" -and
          $_.timestamp -and
          ($_.PSObject.Properties.Name -notcontains "kind") -and  # ignora WATCHER_ERROR
          ([datetime]$_.timestamp) -ge $cutoff -and
          [int]$_.window_hours -eq [int]$WindowHours -and
          $_.severity -ne "ERROR" # opcional: no contaminar baseline con fallos
        }
      )
    }

    if ($baselineItems.Count -ge 3) {
      # Promedios baseline
      $avgTotalJobs  = [double](($baselineItems | Measure-Object -Property total_jobs   -Average).Average)
      $avgTimeout    = [double](($baselineItems | Measure-Object -Property timeout_rate -Average).Average)
      $avgCancel     = [double](($baselineItems | Measure-Object -Property cancel_rate  -Average).Average)

      # Deltas
      $deltaJobs   = $null
      if ($null -ne $totalJobs -and $avgTotalJobs -gt 0) {
        $deltaJobs = ([double]$totalJobs - $avgTotalJobs) / $avgTotalJobs
      }

      $deltaTimeout = $timeoutRate - $avgTimeout
      $deltaCancel  = $cancelRate  - $avgCancel

      # Guardar baseline en payload (latest + history)
      $outPayload.baseline_days = $baselineDays
      $outPayload.baseline_n = $baselineItems.Count
      $outPayload.baseline_avg_total_jobs = [math]::Round($avgTotalJobs, 3)
      $outPayload.baseline_avg_timeout_rate = [math]::Round($avgTimeout, 6)
      $outPayload.baseline_avg_cancel_rate  = [math]::Round($avgCancel, 6)
      $outPayload.baseline_delta_timeout_rate = [math]::Round($deltaTimeout, 6)
      $outPayload.baseline_delta_cancel_rate  = [math]::Round($deltaCancel, 6)
      if ($null -ne $deltaJobs) {
        $outPayload.baseline_delta_total_jobs = [math]::Round($deltaJobs, 6)  # ratio
      }

      # Reglas de desviación (robustas y simples)
      # - drop volumen fuerte: <= -50% vs baseline (si baseline >= 10)
      if ($avgTotalJobs -ge 10 -and $null -ne $deltaJobs -and $deltaJobs -le -0.50) {
        $hasWarning = $true
        Write-Log ("WARNING: volume drop >= 50% vs baseline (total_jobs={0}, baseline_avg={1})" -f $totalJobs, $avgTotalJobs)
      }

      # - spike rates: aumento absoluto fuerte vs baseline
      if ($deltaTimeout -ge 0.15) {
        $hasWarning = $true
        Write-Log ("WARNING: timeout_rate spike vs baseline (current={0}, baseline_avg={1}, delta={2})" -f $timeoutRate, $avgTimeout, $deltaTimeout)
      }

      if ($deltaCancel -ge 0.08) {
        $hasWarning = $true
        Write-Log ("WARNING: cancel_rate spike vs baseline (current={0}, baseline_avg={1}, delta={2})" -f $cancelRate, $avgCancel, $deltaCancel)
      }

      Write-Log ("KPI BASELINE | days={0} n={1} | avg_total_jobs={2} | avg_timeout_rate={3} | avg_cancel_rate={4}" -f $baselineDays, $baselineItems.Count, $avgTotalJobs, $avgTimeout, $avgCancel)
    }
    else {
      # No suficientes puntos para baseline
      $outPayload.baseline_days = $baselineDays
      $outPayload.baseline_n = $baselineItems.Count
      Write-Log ("KPI BASELINE | insufficient history (need>=3) | n={0}" -f $baselineItems.Count)
    }

    # ==========================
    # BASELINE BY DAY-OF-WEEK (DOW) (LAST 7 DAYS)
    # ==========================
    try {
      $now = Get-Date
      $targetDow = $now.DayOfWeek  # Monday, Tuesday, ...

      # opcional: filtrar por hora similar (±2 horas)
      $useHourWindow = $true
      $hourWindow = 2
      $targetHour = $now.Hour

      $dowItems = @()

      if (Test-Path $historyPath) {
        $dowItems = @(
          Get-Content -Path $historyPath -ErrorAction Stop |
          Where-Object { $_ -and $_.Trim().Length -gt 0 } |
          ForEach-Object { try { $_ | ConvertFrom-Json } catch { $null } } |
          Where-Object {
            if ($_ -eq $null) { return $false }
            if (-not ($_.PSObject.Properties.Name -contains "timestamp")) { return $false }
            if (-not $_.timestamp) { return $false }
            if ($_.PSObject.Properties.Name -contains "kind") { return $false } # ignora WATCHER_ERROR

            $t = [datetime]$_.timestamp

            # últimos 7 días + mismo window_hours + no ERROR
            if ($t -lt $cutoff) { return $false }
            if ([int]$_.window_hours -ne [int]$WindowHours) { return $false }
            if ($_.severity -eq "ERROR") { return $false }

            # mismo día de semana
            if ($t.DayOfWeek -ne $targetDow) { return $false }

            # hora aproximada (opcional)
            if ($useHourWindow) {
              $dh = [math]::Abs($t.Hour - $targetHour)
              if ($dh -gt $hourWindow) { return $false }
            }

            return $true
          }
        )
      }

      $outPayload.baseline_dow = "$targetDow"
      $outPayload.baseline_dow_days = $baselineDays
      $outPayload.baseline_dow_hour_window = ($(if($useHourWindow){$hourWindow}else{$null}))

      if ($dowItems.Count -ge 2) {
        $avgDowJobs   = [double](($dowItems | Measure-Object -Property total_jobs   -Average).Average)
        $avgDowTO     = [double](($dowItems | Measure-Object -Property timeout_rate -Average).Average)
        $avgDowCancel = [double](($dowItems | Measure-Object -Property cancel_rate  -Average).Average)

        $deltaDowJobs = $null
        if ($null -ne $totalJobs -and $avgDowJobs -gt 0) {
          $deltaDowJobs = ([double]$totalJobs - $avgDowJobs) / $avgDowJobs
        }

        $deltaDowTO     = $timeoutRate - $avgDowTO
        $deltaDowCancel = $cancelRate  - $avgDowCancel

        $outPayload.baseline_dow_n = $dowItems.Count
        $outPayload.baseline_dow_avg_total_jobs = [math]::Round($avgDowJobs, 3)
        $outPayload.baseline_dow_avg_timeout_rate = [math]::Round($avgDowTO, 6)
        $outPayload.baseline_dow_avg_cancel_rate  = [math]::Round($avgDowCancel, 6)
        $outPayload.baseline_dow_delta_timeout_rate = [math]::Round($deltaDowTO, 6)
        $outPayload.baseline_dow_delta_cancel_rate  = [math]::Round($deltaDowCancel, 6)
        if ($null -ne $deltaDowJobs) {
          $outPayload.baseline_dow_delta_total_jobs = [math]::Round($deltaDowJobs, 6)
        }

        # Reglas (más conservadoras que baseline general)
        if ($avgDowJobs -ge 10 -and $null -ne $deltaDowJobs -and $deltaDowJobs -le -0.50) {
          $hasWarning = $true
          Write-Log ("WARNING: DOW volume drop >= 50% (total_jobs={0}, dow_baseline_avg={1})" -f $totalJobs, $avgDowJobs)
        }

        if ($deltaDowTO -ge 0.12) {
          $hasWarning = $true
          Write-Log ("WARNING: DOW timeout_rate spike (current={0}, dow_avg={1}, delta={2})" -f $timeoutRate, $avgDowTO, $deltaDowTO)
        }

        if ($deltaDowCancel -ge 0.06) {
          $hasWarning = $true
          Write-Log ("WARNING: DOW cancel_rate spike (current={0}, dow_avg={1}, delta={2})" -f $cancelRate, $avgDowCancel, $deltaDowCancel)
        }

        Write-Log ("KPI BASELINE DOW | dow={0} n={1} | avg_total_jobs={2} | avg_timeout_rate={3} | avg_cancel_rate={4}" -f $targetDow, $dowItems.Count, $avgDowJobs, $avgDowTO, $avgDowCancel)
      }
      else {
        $outPayload.baseline_dow_n = $dowItems.Count
        Write-Log ("KPI BASELINE DOW | insufficient history (need>=2) | dow={0} n={1}" -f $targetDow, $dowItems.Count)
      }
    }
    catch {
      $hasWarning = $true
      Write-Log ("WARNING: baseline DOW compute failed. " + $_.Exception.Message)
    }
  }
  catch {
    $hasWarning = $true
    Write-Log ("WARNING: baseline weekly compute failed. " + $_.Exception.Message)
  }

  # Recalcular severidad final despues de reglas/baseline.
  $sev = "SUCCESS"
  if ($hasError) { $sev = "ERROR" }
  elseif ($hasWarning) { $sev = "WARNING" }
  $outPayload.severity = $sev

  Write-Log ("RESUMEN FINAL | severity={0} | snapshot_id={1} | rows_deleted={2} | total_snapshots={3}" -f $sev, $snapshotId, $rowsDeleted, $totalSnapshots)

  $txt = @"
NODO KPI ALERT LATEST
timestamp=$nowIso
severity=$sev
snapshot_id=$snapshotId
window_hours=$WindowHours
total_jobs=$totalJobs
stuck=$stuck
timeout_rate=$timeoutRate
cancel_rate=$cancelRate
"@

  Set-Content -Path $alertTxt -Value $txt -Encoding UTF8

  # Write latest.json atomically (temp -> move) to avoid OneDrive / partial-write issues
  $tmpJson = "$alertJson.tmp"
  ($outPayload | ConvertTo-Json -Depth 6) | Set-Content -Path $tmpJson -Encoding UTF8
  Move-Item -Path $tmpJson -Destination $alertJson -Force

  if (-not (Test-Path $alertJson)) {
    Write-Log "ALERTS ERROR: latest.json missing after write (path=$alertJson)"
  } else {
    $len = (Get-Item $alertJson).Length
    Write-Log ("ALERTS JSON OK: bytes={0}" -f $len)
  }
  Write-Log "ALERTS: wrote alerts/kpi_alert_latest.txt and alerts/kpi_alert_latest.json"

  # ==========================
  # ALERT HISTORY (NDJSON APPEND)
  # ==========================
  try {
    # Reutilizamos el mismo payload que ya estás guardando como JSON "latest"
    # Aseguramos formato compacto 1-line para NDJSON
    $line = ($outPayload | ConvertTo-Json -Depth 10 -Compress)

    # Append con UTF8 estable
    Add-Content -Path $AlertHistory -Value $line -Encoding UTF8

    Write-Log "ALERTS: appended alerts/kpi_alert_history.ndjson"
  }
  catch {
    Write-Log ("ALERTS WARNING: could not append history NDJSON. " + $_.Exception.Message)
  }

  # ===== Copy latest log =====
  Copy-Item -Path $LogFile -Destination $LogLatestFile -Force
  Write-Log "LOGS: wrote logs/kpi_daily_latest.log"

  # ===== Rotate logs =====
  Rotate-Logs -KeepDays $LogsKeepDays
  Write-Log "LOGS: rotation done"

  if ($hasError) {
    Write-Log "=== FAIL (exit 1) ==="

    Write-KpiEventLogError -Message "KPI DAILY FAILED (hasError=$hasError)" -LogPath $LogLatestFile -AlertJsonPath $alertJson | Out-Host

    # ==========================
    # EMAIL ALERT (ERROR ONLY)
    # ==========================
    try {
      $subj = "NODO KPI ERROR | snapshot_id=$snapshotId | $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
      $body = @"
NODO KPI ERROR
timestamp: $(Get-Date -Format "yyyy-MM-ddTHH:mm:ssK")
snapshot_id: $snapshotId
window_hours: $WindowHours
total_jobs: $totalJobs
stuck: $stuck
timeout_rate: $timeoutRate
cancel_rate: $cancelRate

latest.json:
$(Get-Content (Join-Path $AlertsDir "kpi_alert_latest.json") -Raw)
"@
      Send-AlertEmail -Subject $subj -Body $body
    }
    catch {
      Write-Log ("EMAIL WARNING: failed building/sending email. " + $_.Exception.Message)
    }

    Invoke-KpiRetentionSafe
    Release-Lock
    exit 1
  }

  if ($hasWarning) {
    Write-Log "=== WARNING (exit 0) ==="
    Invoke-KpiRetentionSafe
    Release-Lock
    exit 0
  }

  Write-Log "=== SUCCESS (exit 0) ==="
  $LogPath = $LogLatestFile
  $AlertJsonPath = $alertJson
  $okMsg = "DAILY_OK | ts=$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss')) | log=$($LogPath) | alert_json=$($AlertJsonPath)"
  Write-KpiEventLogInfo -Message $okMsg -EventId 9002
  Invoke-KpiRetentionSafe
  Release-Lock
  exit 0
}
catch {
  # En caso de excepción: dejamos una alerta ERROR también
  $msg = $_.Exception.Message
  Write-Log ("FATAL: " + $msg)

  $nowIso = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
  $alertTxt  = Join-Path $AlertsDir "kpi_alert_latest.txt"
  $alertJson = Join-Path $AlertsDir "kpi_alert_latest.json"

  $txt = @"
NODO KPI ALERT LATEST
timestamp=$nowIso
severity=ERROR
snapshot_id=$snapshotId
window_hours=$WindowHours
total_jobs=$totalJobs
stuck=$stuck
timeout_rate=$timeoutRate
cancel_rate=$cancelRate
fatal=$msg
"@
  Set-Content -Path $alertTxt -Value $txt -Encoding UTF8

  $outPayload = [ordered]@{
    timestamp    = $nowIso
    severity     = "ERROR"
    snapshot_id  = $snapshotId
    window_hours = $WindowHours
    total_jobs   = $totalJobs
    stuck        = $stuck
    timeout_rate = $timeoutRate
    cancel_rate  = $cancelRate
    fatal        = $msg
  }
  ($outPayload | ConvertTo-Json -Depth 6) | Set-Content -Path $alertJson -Encoding UTF8

  # ==========================
  # ALERT HISTORY (NDJSON APPEND)
  # ==========================
  try {
    # Reutilizamos el mismo payload que ya estás guardando como JSON "latest"
    # Aseguramos formato compacto 1-line para NDJSON
    $line = ($outPayload | ConvertTo-Json -Depth 10 -Compress)

    # Append con UTF8 estable
    Add-Content -Path $AlertHistory -Value $line -Encoding UTF8

    Write-Log "ALERTS: appended alerts/kpi_alert_history.ndjson"
  }
  catch {
    Write-Log ("ALERTS WARNING: could not append history NDJSON. " + $_.Exception.Message)
  }

  Copy-Item -Path $LogFile -Destination $LogLatestFile -Force -ErrorAction SilentlyContinue

  Write-KpiEventLogError -Message ("KPI DAILY FAILED (exception): " + $msg) -LogPath $LogLatestFile -AlertJsonPath $alertJson | Out-Host

  # ==========================
  # EMAIL ALERT (ERROR ONLY)
  # ==========================
  try {
    $subj = "NODO KPI ERROR | snapshot_id=$snapshotId | $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    $body = @"
NODO KPI ERROR
timestamp: $(Get-Date -Format "yyyy-MM-ddTHH:mm:ssK")
snapshot_id: $snapshotId
window_hours: $WindowHours
total_jobs: $totalJobs
stuck: $stuck
timeout_rate: $timeoutRate
cancel_rate: $cancelRate

latest.json:
$(Get-Content (Join-Path $AlertsDir "kpi_alert_latest.json") -Raw)
"@
    Send-AlertEmail -Subject $subj -Body $body
  }
  catch {
    Write-Log ("EMAIL WARNING: failed building/sending email. " + $_.Exception.Message)
  }

  Invoke-KpiRetentionSafe
  Release-Lock
  exit 1
}
