# CHECKPOINT HARD FINAL -- LIFECYCLE CONTRACT + RUNTIME GUARD

Fecha: 2026-03-16

Estado: LOCKED / STABLE

## Alcance congelado

Este checkpoint cubre exclusivamente el frente de contrato de lifecycle y blindaje anti-regresiones de mutaciones de estado en runtime.

## Cadena estructural confirmada

1. Decision canonica
2. Contrato central
3. Migracion runtime
4. Tests
5. Guard automatico
6. Workflow minimo

## Regla canonica (runtime)

- Cambios de `job_status` solo por `transition_job_status(...)`.
- Cambios de `assignment_status` solo por `transition_assignment_status(...)`.
- Reactivacion legacy de assignment solo por helper explicito del contrato central.

## Blindaje automatico activo

Archivo:

- `scripts/guard_no_direct_status_writes.ps1`

Detecta solo mutaciones directas reales fuera del contrato central:

- `job.job_status = ...`
- `assignment.assignment_status = ...`
- `update(job_status=...)`
- `update(assignment_status=...)`

Ignora comparaciones y lecturas (`==`, `in`, etc.) por regex de asignacion real.

Exclusion explicita:

- `jobs/services_state_transitions.py` (archivo canonico permitido)

## CI minimo activo

Archivo:

- `.github/workflows/lifecycle-contract-guard.yml`

Ejecucion:

- checkout
- run `./scripts/guard_no_direct_status_writes.ps1`

Sin pasos extra en este frente.

## Verificacion de cierre

- Ejecucion local del guard: PASS
- Resultado: no direct runtime status writes found

## Criterio de regresion

Si aparece cualquier mutacion directa de estado fuera del contrato central, el guard falla y el workflow marca error.

## Tag de referencia sugerido

- `NODO_LIFECYCLE_CONTRACT_GUARD_LOCKED_V1`
