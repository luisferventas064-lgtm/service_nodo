# CHECKPOINT -- ASSIGNMENT INTEGRITY GUARD

Fecha: 2026-03-16

Estado: CREATED / EXECUTED

## Guard file

- `scripts/guard_assignment_integrity.py`

## Reglas validadas por el guard

1. maximo 1 assignment activo por job
2. `pending_client_confirmation` no puede tener assignment activo
3. si existe assignment activo, debe ser coherente con el estado del job
4. `selected_provider_id` debe alinearse con provider del assignment activo
5. assignment `cancelled` no puede permanecer activo

## Ajuste de runtime aplicado para soportar la regla

Se alineo runtime marketplace para preservar `selected_provider_id` al confirmar provider y pasar a `assigned`.

Motivo:

- sin ese ajuste, la regla `selected_provider <-> active assignment` generaba falsos positivos en jobs sanos de marketplace

## Ejecucion manual

Comando:

`./.venv/Scripts/python.exe scripts/guard_assignment_integrity.py`

## Resultado actual

Resultado: FAIL

Salida real observada:

- `ASSIGNMENT INTEGRITY GUARD FAILED`
- 9 hallazgos `MISSING_ACTIVE_ASSIGNMENT_FOR_JOB_STATUS`

Jobs detectados:

- `job=899`
- `job=900`
- `job=901`
- `job=902`
- `job=903`
- `job=905`
- `job=907`
- `job=909`
- `job=910`

Todos con patron:

- `status=assigned`
- sin assignment activo

## Impacto operacional

Impacto: REAL / datos

Interpretacion:

- no es fallo del guard
- no es ruido de tooling
- son registros que violan el contrato canonico actual de integridad

## Decision

1. mantener el guard como detector read-only
2. no mutar automaticamente datos desde el guard
3. tratar los 9 jobs detectados como backlog de reparacion/investigacion
4. dejar CI preparada para smoke/integration del guard con SQLite efimero + migrate

## Validacion complementaria

Tests focalizados tras alinear runtime:

- `jobs.test_marketplace_client_confirmation`
- `ui.test_marketplace_client_confirmation_runtime`

Resultado:

- 10 tests OK

## Siguiente paso recomendado

1. investigar los 9 jobs `assigned` sin assignment activo
2. clasificar si son legado historico o corrupcion runtime reciente
3. si hace falta, crear runbook de reparacion manual controlada para esos casos
