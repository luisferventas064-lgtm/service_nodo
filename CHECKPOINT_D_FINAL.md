# CHECKPOINT -- MODULE D -- LEDGER + EVIDENCE + OPS

Fecha: 2026-02-24

Estado: LOCKED / STABLE

## Componentes incluidos

- PlatformLedgerEntry (modelo financiero)
- Freeze real (is_final, finalized_at, run_id, version)
- Rebuild auditado (rebuild_count + trazabilidad)
- Proteccion por estado (finalize + rebuild)
- Evidence pack JSON automatico
- Ops commands:
  - ledger_status
  - ledger_rebuild
  - ledger_finalize
  - ledger_backfill
  - ledger_evidence

## Garantias del sistema

- Todo job tiene ledger
- Ningun ledger puede congelarse en estado invalido
- Rebuild no rompe freeze
- Toda mutacion financiera genera evidencia
- Sistema idempotente y auditado

Tag asociado:
NODO_LEDGER_V1_LOCKED
