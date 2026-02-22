# RUNBOOK - NODO KPI (Daily / Weekly / Watcher)

## Quick Health
- Run:
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\kpi_health.ps1

- Dashboard:
  type .\reports\kpi_status_latest.md
  (Get-Content .\reports\kpi_status_latest.json -Raw | ConvertFrom-Json).overall_status

## Tasks (Scheduler)
- Query:
  schtasks /Query /TN "NODO_KPI_DAILY" /V /FO LIST
  schtasks /Query /TN "NODO_KPI_WEEKLY_REPORT" /V /FO LIST
  schtasks /Query /TN "NODO_KPI_WATCHER" /V /FO LIST

- Run now:
  schtasks /Run /TN "NODO_KPI_DAILY"
  schtasks /Run /TN "NODO_KPI_WEEKLY_REPORT"
  schtasks /Run /TN "NODO_KPI_WATCHER"

## Logs / Alerts / Reports
- Logs:
  logs\kpi_daily_latest.log
  logs\kpi_weekly_latest.log
  logs\kpi_watcher_latest.log

- Alerts:
  alerts\kpi_alert_latest.json
  alerts\kpi_alert_latest.txt
  alerts\kpi_alert_history.ndjson

- Reports:
  reports\kpi_weekly_YYYY-MM-DD.md / .json
  reports\kpi_status_latest.md / .json

## Event Viewer (Application)
Source: NODO-KPI
- Real:
  9000 WEEKLY_OK
  9002 DAILY_OK
  9001 DAILY_ERROR (si aplica)
  9010 WATCHER_OK
  9011 WATCHER_ERROR
- Test:
  12410 TEST WATCHER_OK
  12411 TEST WATCHER_ERROR

PowerShell:
Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='NODO-KPI'} -MaxEvents 25 |
  Select-Object TimeCreated, Id, LevelDisplayName, Message

## Common Incidents

### 1) DAILY_LOG_MISSING
- Check:
  Test-Path .\logs\kpi_daily_latest.log
- Action:
  powershell -File scripts\kpi_daily.ps1
  Review Task NODO_KPI_DAILY (Last Result, Start in, lock file).

### 2) DAILY_LOG_STALE
- Means daily hasn't updated within threshold.
- Action:
  schtasks /Run /TN "NODO_KPI_DAILY"
  Tail log:
  Get-Content .\logs\kpi_daily_latest.log -Tail 200

### 3) ALERT_JSON_INDICATES_ERROR
- Action:
  type .\alerts\kpi_alert_latest.txt
  type .\alerts\kpi_alert_latest.json
  Review daily log + weekly report for context.

### 4) SMTP / AUTH errors
- Check secrets:
  type .\secrets\nodo_smtp.env
- Gmail requires App Password (16 chars). Re-test:
  powershell -File scripts\kpi_weekly.ps1

### 5) EventLog write failures
- Ensure Source exists (created once as Admin) and permissions ok.

## Safe Test Run (Watcher)
powershell -File scripts\kpi_watcher.ps1 -MaxSilenceHours 0 -TestRun
