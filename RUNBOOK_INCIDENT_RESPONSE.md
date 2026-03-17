# RUNBOOK INCIDENT RESPONSE -- NODO

Fecha: 2026-03-16

Estado: ACTIVE / OPERATIONAL

## 1. Purpose

Este runbook define respuesta operativa para incidentes runtime en:

1. lifecycle de Job
2. integridad de JobAssignment
3. marketplace confirmation/timeout

Objetivo:

- detectar rapido
- contener impacto
- aplicar correccion segura
- dejar evidencia auditable

## 2. Scope

Incluye:

- marketplace (`scheduled`)
- urgency/on-demand (`on_demand`)
- transiciones Job + JobAssignment

No incluye:

- incidentes financieros avanzados (ledger/tickets) fuera de estados
- incidentes de infraestructura externa (DB outage, SMTP outage)

## 3. Core invariants (do-not-break)

1. Mutaciones de estado solo via contrato central:
   - `transition_job_status(...)`
   - `transition_assignment_status(...)`
2. Marketplace:
   - `provider_accept -> pending_client_confirmation`
   - `client_confirm -> assigned`
3. En salidas desde `pending_client_confirmation` sin confirmacion:
   - limpiar `selected_provider`
   - limpiar assignment activo inesperado
4. Estados terminales no mutan en runtime normal:
   - `confirmed`, `cancelled`, `expired`

## 4. Triage checklist (siempre)

Antes de actuar:

1. Identificar `job_id`
2. Confirmar `job_mode`
3. Confirmar `job_status`
4. Revisar `selected_provider_id`
5. Revisar assignment(s) activos
6. Revisar timestamps:
   - `client_confirmation_started_at`
   - `next_marketplace_alert_at`
   - `marketplace_search_started_at`
7. Revisar ultimo evento timeline (tipo + nota + source)

## 5. Incident playbooks

### 5.1 Job stuck in waiting_provider_response

Sintoma:

- Job permanece demasiado tiempo en `waiting_provider_response`

Checks:

1. ¿`job_mode` es `scheduled` o `on_demand`?
2. ¿`next_marketplace_alert_at`/`next_alert_at` estan vencidos?
3. ¿existen intentos de broadcast (`JobBroadcastAttempt`)?
4. ¿el job tiene exclusion de providers que deja pool vacio?

Accion:

1. Ejecutar tick correspondiente:
   - marketplace: `tick_marketplace`
   - on-demand: `tick_on_demand`
2. Si pool esta vacio por reglas de negocio, escalar a operacion (no forzar asignacion manual).

No hacer:

- no mutar `job_status` directo en DB
- no usar endpoint legacy de asignacion directa

### 5.2 Pending client confirmation timeout anomaly

Sintoma:

- Job en `pending_client_confirmation` fuera de ventana

Checks:

1. `client_confirmation_started_at` existe
2. ventana de 60m vencida
3. resultado esperado:
   - `waiting_provider_response` (reopen), o
   - `pending_client_decision` (si vence ventana total)

Accion:

1. Ejecutar `tick_marketplace`
2. Verificar post-condicion:
   - provider limpiado
   - assignment activo inesperado desactivado

### 5.3 Provider accepted but job not assigned

Sintoma:

- Provider acepto oferta marketplace, job sigue no-assigned

Checks:

1. confirmar si job esta en `pending_client_confirmation` (esperado)
2. validar si cliente confirmo provider
3. validar timeout/rechazo/cancel posterior

Accion:

1. Si cliente confirma: usar accion runtime de confirmacion cliente
2. Si vencio ventana: permitir timeout canonico (tick)

No hacer:

- no forzar `assigned` directo por SQL

### 5.4 Assignment active but job not assigned

Sintoma:

- existe `JobAssignment.is_active=True` pero job no esta en `assigned`/`in_progress`

Checks:

1. revisar si job esta en `pending_client_confirmation`, `waiting_provider_response` o `cancelled`
2. revisar ultimo evento (reject/cancel/timeout)
3. confirmar si limpieza defensiva debio ejecutarse

Accion:

1. tratar como inconsistencia de integridad
2. aplicar correccion por contrato (cancelar assignment via transición canonica)
3. registrar incidente y causa raiz

### 5.5 Duplicate active assignment for same job

Sintoma:

- mas de un assignment activo para mismo job

Checks:

1. contar assignments activos por `job_id`
2. identificar assignment canonico (provider seleccionado y ultimo flujo valido)

Accion:

1. mantener solo uno activo
2. cancelar los demas por transición canonica
3. validar coherencia de `selected_provider_id`

Escalacion:

- abrir bug de integridad inmediatamente

## 6. Operational commands

Comandos base (desde repo):

1. `python manage.py tick_marketplace`
2. `python manage.py tick_on_demand`
3. `python manage.py tick_scheduled_activation`
4. `python manage.py tick_all`

## 7. Evidence required per incident

Registrar siempre:

1. `job_id`
2. estado inicial y final
3. timestamps relevantes
4. eventos timeline antes/despues
5. assignments antes/despues
6. comando/accion aplicada
7. decision de cierre (fixed / monitoring / escalated)

## 8. Escalation policy

Escalar a development inmediatamente si:

1. hay mutacion de estado fuera de contrato
2. reaparece endpoint/ruta legacy para asignar directo
3. hay duplicados activos de assignment repetidos
4. timeout no limpia provider/assignment en pending confirmation

## 9. Related references

- `CHECKPOINT_HARD_FINAL.md`
- `CHECKPOINT_MARKETPLACE_OPERATIONAL_CONTRACT_V2.md`
- `CHECKPOINT_PENDING_CLIENT_CONFIRMATION_TIMEOUT_HARD_CHECK.md`
- `CHECKPOINT_JOB_ASSIGNMENT_FUNCTIONAL_MATRIX_RUNBOOK.md`
- `CHECKPOINT_MARKETPLACE_CHECKER_RECONCILIATION.md`
