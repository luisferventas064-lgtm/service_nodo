# CHECKPOINT -- JOB + JOBASSIGNMENT FUNCTIONAL MATRIX RUNBOOK

Fecha: 2026-03-16

Estado: LOCKED / STABLE (operational reference)

## 1) Purpose

Este documento es una matriz funcional operativa para soporte y runbooks.
No reemplaza arquitectura; prioriza diagnostico rapido:

1. estado actual
2. significado operativo
3. actor que puede moverlo
4. destinos permitidos
5. que revisar cuando algo falla

## 2) Job Matrix

| Job status | Significado operativo | Quien lo mueve | Siguiente permitido | Que revisar en runbook |
| --- | --- | --- | --- | --- |
| `scheduled_pending_activation` | Job programado, aun no entra a busqueda activa | scheduler / activacion programada | `waiting_provider_response` | fecha/hora programada, tick de activacion, elegibilidad |
| `waiting_provider_response` | Job abierto esperando respuesta de provider | sistema marketplace / timeout / reopen | `assigned`, `expired`, `cancelled`, `pending_client_confirmation`* | candidate pool, ranking/broadcast, expiracion, selected provider, ventanas |
| `pending_client_confirmation` | Provider acepto en marketplace y cliente debe decidir | provider accept marketplace / cliente / timeout | `assigned`, `waiting_provider_response`, `cancelled`, `pending_client_decision`** | selected provider, ventana de confirmacion, limpieza assignment inesperado |
| `pending_client_decision` | Estado intermedio de decision cliente (compatibilidad) | timeout/flujo legacy controlado | compatibilidad (no camino preferido) | validar si viene de timeout 24h o rama legacy |
| `assigned` | Provider formalmente asignado | confirmacion cliente marketplace / flujo canonico on-demand | `in_progress`, `cancelled` | assignment activo, provider correcto, integridad selected provider |
| `in_progress` | Servicio iniciado | provider / runtime ejecucion | `completed`, `cancelled` | autorizacion start, assignment activo, timestamps |
| `completed` | Provider termino trabajo; pendiente cierre final | provider / sistema | `confirmed` | evidencia de completion y cierre cliente |
| `confirmed` | Trabajo cerrado definitivamente | cliente / auto-confirm | ninguno | estado terminal, no debe mutar |
| `expired` | No se logro provider en ventana operativa | timeout/scheduler | ninguno canonico | ventanas, olas broadcast, politica de reopen |
| `cancelled` | Job cancelado | cliente / sistema | ninguno canonico | causa, momento, side effects |
| `posted` | Estado legacy/deprecado | no debe escribirse nuevo | se normaliza a `waiting_provider_response` | tratar como entrada legacy |
| `draft`*** | Borrador / pre-runtime si existiera | fuera de runtime canonico principal | fuera de alcance canonico | confirmar existencia real en runtime |

\* En marketplace canonico, `provider_accept` lleva primero a `pending_client_confirmation`.

\** Se mantiene como salida de compatibilidad (ej. timeout de ventana total), no como camino preferido.

\*** Mantener en runbook solo como referencia si existe en datos heredados.

## 3) JobAssignment Matrix

| Assignment status | Significado operativo | Quien lo mueve | Siguiente permitido | Que revisar en runbook |
| --- | --- | --- | --- | --- |
| `assigned` | Assignment activo listo para ejecucion | sistema al confirmar asignacion | `in_progress`, `cancelled` | provider correcto, `is_active`, vinculo con job |
| `in_progress` | Assignment en ejecucion | provider / runtime | `completed`, `cancelled` | autorizacion de inicio, assignment unico activo |
| `completed` | Assignment terminado | provider / sistema | ninguno canonico | integridad con `job.completed` |
| `cancelled` | Assignment cancelado | sistema / cliente / fallback defensivo | ninguno canonico | motivo y limpieza de activo |

## 4) Event Matrix

| Evento operativo | Job origen | Job destino | Efecto en assignment |
| --- | --- | --- | --- |
| Activacion programada | `scheduled_pending_activation` | `waiting_provider_response` | ninguno |
| Provider accept marketplace | `waiting_provider_response` | `pending_client_confirmation` | no crear assignment activo todavia |
| Cliente confirma provider marketplace | `pending_client_confirmation` | `assigned` | crear/activar assignment |
| Cliente rechaza provider | `pending_client_confirmation` | `waiting_provider_response` | limpiar assignment inesperado |
| Cliente cancela | `pending_client_confirmation` o `waiting_provider_response` | `cancelled` | cancelar/limpiar si aplica |
| Timeout confirmacion cliente | `pending_client_confirmation` | `waiting_provider_response` o `pending_client_decision` | limpiar assignment inesperado |
| Timeout busqueda | `waiting_provider_response` | `expired` | ninguno |
| Provider start | `assigned` | `in_progress` | `assigned -> in_progress` |
| Provider complete | `in_progress` | `completed` | `in_progress -> completed` |
| Cliente confirma cierre | `completed` | `confirmed` | sin cambio canonico posterior |

## 5) Invariants (Do Not Break)

1. Mutaciones de estado solo por contrato central:
   - `transition_job_status(...)`
   - `transition_assignment_status(...)`
2. `posted` no es destino nuevo; si aparece, normalizar como `waiting_provider_response`.
3. Marketplace:
   - `provider_accept` != `assigned`
   - `provider_accept -> pending_client_confirmation`
   - `client_confirm -> assigned`
4. Toda salida desde `pending_client_confirmation` que no confirma:
   - limpia `selected_provider`
   - limpia assignment activo inesperado
   - limpia campos temporales de confirmacion
5. Estados terminales no mutan en runtime canonico:
   - `confirmed`
   - `cancelled`
   - `expired`
   - `assignment.completed`

## 6) Runbook Symptoms

### Si un job "se quedo pegado"

Revisar en orden:

1. `job_status`
2. `job_mode`
3. `selected_provider_id`
4. assignment activo
5. timestamps de timeout/confirmacion
6. ultimo evento timeline
7. si entro por marketplace u urgency

### Si existe assignment activo en estado raro

1. validar si job esta en `pending_client_confirmation`
2. validar si debio limpiarse por reject/cancel/timeout
3. determinar si es legado inconsistente
4. validar que la mutacion paso por contrato central

### Si cliente reporta "ya confirme"

1. job en `pending_client_confirmation`
2. accion runtime en `request_status_view`
3. transicion a `assigned`
4. creacion/activacion de assignment
5. provider seleccionado correcto

## 7) Canonical Freeze Snapshot

### Job canonical

`scheduled_pending_activation -> waiting_provider_response -> pending_client_confirmation (marketplace only) -> assigned -> in_progress -> completed -> confirmed`

Side exits:

- `waiting_provider_response -> expired`
- `waiting_provider_response -> cancelled`
- `pending_client_confirmation -> waiting_provider_response`
- `pending_client_confirmation -> cancelled`
- `pending_client_confirmation -> pending_client_decision` (compatibilidad / timeout ventana total)

### JobAssignment canonical

`assigned -> in_progress -> completed`

Side exits:

- `assigned -> cancelled`
- `in_progress -> cancelled`

## 8) Related checkpoints

- `CHECKPOINT_HARD_FINAL.md`
- `CHECKPOINT_MATCHING_BROADCAST_HARD_CHECK.md`
- `CHECKPOINT_MARKETPLACE_CONFIRMATION_CONTRACT_HARD.md`
- `CHECKPOINT_PENDING_CLIENT_CONFIRMATION_TIMEOUT_HARD_CHECK.md`
- `CHECKPOINT_MARKETPLACE_OPERATIONAL_CONTRACT_V2.md`
- `CHECKPOINT_MARKETPLACE_CHECKER_RECONCILIATION.md`
