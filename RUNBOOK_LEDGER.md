# RUNBOOK — NODO Ledger (D)

## Objetivo
Ledger interno por Job (sin pagos externos):
- snapshot final (freeze) al cerrar servicio
- rebuild controlado con auditoria (sin romper freeze por defecto)
- evidence pack JSON (auto en close/rebuild + command manual)

---

## Componentes
- Modelo: `jobs.models.PlatformLedgerEntry`
- Calculo: `jobs/ledger.py`
  - `upsert_platform_ledger_entry(job_id, force=False)`
  - `finalize_platform_ledger_for_job(job_id, run_id=None)`
  - `rebuild_platform_ledger_for_job(job_id, run_id=None, reason=None)`
- Evidence: `jobs/evidence.py`
  - `build_job_evidence_payload(...)`
  - `write_job_evidence_json(...)`
  - `try_write_job_evidence_json(...)` (best-effort)

---

## Convenciones (cents)
- `line_total_cents` = GROSS (incluye impuesto)
- `tax_cents` = impuesto por linea
- subtotal implicito = `gross - tax`
- ledger:
  - `fee_cents` = fee NET (fee_line_total - fee_line_tax)
  - `platform_revenue_cents` = `fee_cents` (v1)
  - `net_provider_cents` = (provider_gross - provider_tax) - provider_fee_net

Fee line se detecta por:
- `line_type == "fee"` (principal)

---

## Operacion normal (automatica)
### Close / Confirm (client)
Hook en `jobs/services.py -> confirm_service_closed_by_client(...)`:
- recalcula tickets/totals
- `finalize_platform_ledger_for_job(job_id, run_id=...)`
- auto-evidence best-effort: `try_write_job_evidence_json(source="finalize")`

Resultado esperado:
- `PlatformLedgerEntry.is_final = True`
- `finalized_at` set
- `finalized_run_id` poblado con `AUTO_CLOSE_..._job_<ID>`
- se escribe evidence JSON (si no falla filesystem)

---

## Commands (manual / ops)
### 1) Generar Evidence Pack manual
```powershell
python manage.py ledger_evidence --job-id <ID>
python manage.py ledger_evidence --job-id <ID> --out-dir "C:\temp\nodo_evidence" --source finalize --run-id "RUN_..."
```

### 2) Rebuild auditado (force)
```powershell
python manage.py ledger_rebuild --job-id <ID> --run-id "REBUILD_..." --reason "manual correction"
```

### 3) Finalize (freeze) manual
```powershell
python manage.py ledger_finalize --job-id <ID> --run-id "FINALIZE_..."
```

## Settings
En `config/settings.py`:
```python
NODO_EVIDENCE_DIR = None
```

- `None` => escribe en `<BASE_DIR>/evidence/`
- Si se define => escribe en esa carpeta

## Verificacion rapida en DB (Django shell)
```python
python manage.py shell
from jobs.models import PlatformLedgerEntry
e = PlatformLedgerEntry.objects.get(job_id=<ID>)
e.is_final, e.gross_cents, e.tax_cents, e.fee_cents, e.net_provider_cents, e.platform_revenue_cents
```

## Troubleshooting
- No se creo ledger al cerrar:
  - Revisa que el hook en `confirm_service_closed_by_client` este en ambos caminos (`closed_and_confirmed` y `already_confirmed`).
- Evidence no aparece:
  - Si el filesystem falla, el wrapper best-effort no rompe el flujo; genera evidence manual con `ledger_evidence`.
- Rebuild no cambia numeros:
  - Confirma que modificaste lines/tickets realmente y que `ledger_rebuild` se ejecuto (`rebuild_count` incrementa).
