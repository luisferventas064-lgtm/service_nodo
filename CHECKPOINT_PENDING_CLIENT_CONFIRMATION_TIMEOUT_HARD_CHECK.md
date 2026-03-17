# CHECKPOINT -- PENDING_CLIENT_CONFIRMATION TIMEOUT HARD CHECK

Fecha: 2026-03-16

Estado: LOCKED / STABLE

## 1. Entrypoint real del timeout

El timeout operativo de `pending_client_confirmation` se procesa por:

- comando `tick_marketplace`
- servicio `process_marketplace_client_confirmation_timeout(...)`

Flujo runtime:

1. `jobs/management/commands/tick_marketplace.py`
2. filtra jobs `scheduled` en `pending_client_confirmation` vencidos
3. ejecuta `process_marketplace_client_confirmation_timeout(job_id, now=now)`

## 2. Servicio que ejecuta la decision

Servicio canonico:

- `jobs/services.py` -> `process_marketplace_client_confirmation_timeout(...)`

Decide segun ventana total de busqueda:

- si vence la ventana de busqueda de marketplace: destino `pending_client_decision`
- si no vence la ventana total: destino `waiting_provider_response` (reopen)

## 3. Estado origen exacto

Origen valido:

- `job_mode == scheduled`
- `job_status == pending_client_confirmation`
- `client_confirmation_started_at` presente
- timeout de confirmacion vencido

## 4. Estado destino exacto

Destinos canónicos:

1. `pending_client_confirmation -> waiting_provider_response`
2. `pending_client_confirmation -> pending_client_decision`

No hay salto a `assigned` por timeout.

## 5. Limpieza de selected_provider / assignment

Criterio canónico aplicado en este pase:

- siempre limpiar `selected_provider_id`
- siempre limpiar `client_confirmation_started_at`
- desactivar assignment activo inesperado (defensivo)

Se reforzo en codigo en rutas de salida desde pending confirmation:

- timeout -> waiting
- timeout -> pending_client_decision
- reject de cliente -> waiting
- cancel de cliente -> cancelled

## 6. Reprogramacion o cierre

- reopen a `waiting_provider_response`: reprograma `next_marketplace_alert_at=now`
- a `pending_client_decision`: deja `next_marketplace_alert_at=None` para decision del cliente
- cancel: `cancelled` con campos de marketplace limpiados

## 7. Eventos / timeline

Timeout/reopen registra:

- `TIMEOUT` con nota de causa
- `WAITING_PROVIDER_RESPONSE` cuando corresponde reopen

Rechazo cliente registra:

- `TIMEOUT` semantico de rechazo
- `WAITING_PROVIDER_RESPONSE` con source `reject_marketplace_provider`

Cancel cliente registra:

- `CANCELLED`
- `JOB_CANCELLED` con source `cancel_job`

## 8. Inconsistencias detectadas

Detectada y cerrada en este pase:

- si existia assignment activo inesperado en `pending_client_confirmation`, algunas salidas no lo limpiaban.

Estado actual:

- corregido: limpieza defensiva de assignments en timeout/reject/cancel.

## 9. Recomendacion canonica

Contrato operativo marketplace para este tramo:

1. `provider_accept -> pending_client_confirmation`
2. `client_confirm -> assigned`
3. `client_reject -> waiting_provider_response` + limpieza provider/assignment
4. `client_cancel -> cancelled` + limpieza provider/assignment
5. `timeout -> waiting_provider_response` o `pending_client_decision` + limpieza provider/assignment

No existen rutas canónicas de timeout que salten a `assigned`.

## Evidencia de validacion

Tests ejecutados en este pase:

`python manage.py test jobs.test_marketplace_client_confirmation jobs.test_marketplace_client_decision ui.test_marketplace_client_confirmation_runtime --keepdb --noinput`

Resultado:

- 17 tests
- OK

## Archivos tocados en este pase

- `jobs/services.py`
- `jobs/test_marketplace_client_confirmation.py`
- `jobs/test_marketplace_client_decision.py`
- `ui/test_marketplace_client_confirmation_runtime.py`

## Tag sugerido

- `NODO_PENDING_CLIENT_CONFIRMATION_TIMEOUT_LOCKED_V1`
