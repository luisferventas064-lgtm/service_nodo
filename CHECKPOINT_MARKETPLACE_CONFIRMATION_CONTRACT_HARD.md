# CHECKPOINT HARD FINAL -- MARKETPLACE CONFIRMATION CONTRACT

Fecha: 2026-03-16

Estado: LOCKED / STABLE

## Alcance

Este checkpoint congela exclusivamente el contrato de confirmacion de cliente para marketplace (scheduled), sin mezclar el flujo urgency/on-demand.

## Contrato canónico congelado

Secuencia principal marketplace:

1. `request_create`
2. `waiting_provider_response`
3. `provider_accept`
4. `pending_client_confirmation`
5. `client_confirm`
6. `assigned`

Ramas laterales marketplace:

- `client_reject` -> `waiting_provider_response`
- `client_cancel` -> `cancelled`

## Reglas estructurales

1. En marketplace, `assigned` no puede ocurrir durante `provider_accept`.
2. En marketplace, `assigned` solo ocurre tras confirmacion explicita de cliente.
3. En marketplace, rechazo de cliente limpia provider seleccionado y reabre busqueda.
4. En marketplace, cancelacion de cliente desde pending confirmation termina en `cancelled`.

## Capas alineadas

Dominio:

- `jobs/services.py`
  - `accept_provider_offer(...)`
  - `accept_marketplace_offer(...)`
  - `confirm_marketplace_provider(...)`
  - `reject_marketplace_provider(...)`
  - `apply_client_marketplace_decision(... cancel ...)`

Runtime UI:

- `ui/views.py`
  - `request_status_view` acciones:
    - `confirm_provider`
    - `reject_provider`
    - `cancel_request` desde pending client confirmation

Interfaz:

- `templates/request/status.html`
  - acciones visibles para estado `pending_client_confirmation`:
    - Confirm Provider
    - Reject Provider
    - Cancel Request

## Canonizacion de aceptacion marketplace

- `ui/views_provider.py` ahora enruta aceptacion por `accept_provider_offer(...)`.
- `jobs/services_lifecycle.py` bloquea `accept_job_by_provider(...)` para jobs scheduled.
- Ruta legacy directa deshabilitada:
  - `jobs/views.py` -> `assign_provider`
  - respuesta: `400 legacy_assign_provider_endpoint_disabled`

## Evidencia de validacion

Comando ejecutado:

`python manage.py test ui.test_marketplace_provider_accept_canonical ui.test_marketplace_client_confirmation_runtime jobs.test_marketplace_client_confirmation jobs.test_marketplace_provider_accept_race --keepdb --noinput`

Resultado:

- 21 tests
- OK

## No-objetivos (explicitos)

- No se modifica contrato de urgency/on-demand en este checkpoint.
- No se modifica motor de ranking fuera de la integracion de estados marketplace.

## Riesgo residual conocido

- Verificar alineacion fina del timeout de `pending_client_confirmation` contra todas las ramas de reopen/cancel en escenarios operativos extendidos (siguiente bloque).

## Tag de referencia sugerido

- `NODO_MARKETPLACE_CONFIRMATION_CONTRACT_LOCKED_V1`
