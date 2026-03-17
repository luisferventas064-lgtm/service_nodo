# RUNBOOK - NODO Ticks

## Quick Health
- Run once:
  `.\.venv\Scripts\python.exe manage.py tick_all`

- Scheduler wrapper:
  `scripts\tick_all.cmd`

- Hidden scheduler launcher:
  `scripts\tick_all_hidden.vbs`

- Latest log:
  `logs\tick_all_latest.log`

## Task Scheduler
- Install:
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install_tick_all_scheduler.ps1`

- Install and run now:
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\install_tick_all_scheduler.ps1 -RunNow`

- Query:
  `schtasks /Query /TN "NODO_TICK_ALL" /V /FO LIST`

- Run now:
  `schtasks /Run /TN "NODO_TICK_ALL"`

- Remove:
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\uninstall_tick_all_scheduler.ps1`

## Expected Scope
`tick_all` runs:
- `tick_scheduled_activation`
- `tick_on_demand`
- `tick_marketplace`

## Hidden Execution
- Task action should run:
  `wscript.exe "C:\Users\luisf\Documents\GitHub\service_nodo\scripts\tick_all_hidden.vbs"`

- `tick_all_hidden.vbs` launches `tick_all.ps1` with a hidden window and waits for completion.

- `scripts\tick_all.cmd` remains useful for manual runs from terminal.

## Scheduled Activation Contract
- Future `scheduled` jobs remain in `scheduled_pending_activation`.
- When `scheduled_for <= now`, `tick_scheduled_activation` promotes them to `waiting_provider_response`.
- Timeline should show:
  - `job_created`
  - `scheduled_activated`
  - `waiting_provider_response`

## Troubleshooting
- If the task exists but does not run:
  - `schtasks /Run /TN "NODO_TICK_ALL"`
  - `Get-Content .\logs\tick_all_latest.log -Tail 200`

- If runs overlap:
  - `scripts\tick_all.ps1` uses `logs\tick_all.lock` and skips while another run is active.
