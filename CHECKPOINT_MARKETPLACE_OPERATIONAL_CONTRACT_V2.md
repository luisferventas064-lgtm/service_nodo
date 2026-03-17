# CHECKPOINT HARD FINAL -- MARKETPLACE OPERATIONAL CONTRACT V2

Fecha: 2026-03-16

Estado: LOCKED / STABLE (funcional)

## Objetivo

Consolidar en una sola referencia operativa los subfrentes marketplace ya cerrados:

1. aceptacion marketplace
2. confirmacion marketplace
3. timeout/salidas de `pending_client_confirmation`

## Referencias base (sub-checkpoints)

- `CHECKPOINT_MATCHING_BROADCAST_HARD_CHECK.md`
- `CHECKPOINT_MARKETPLACE_CONFIRMATION_CONTRACT_HARD.md`
- `CHECKPOINT_PENDING_CLIENT_CONFIRMATION_TIMEOUT_HARD_CHECK.md`

## Contrato operativo consolidado

### Camino principal marketplace

1. `request_create`
2. `waiting_provider_response`
3. `provider_accept`
4. `pending_client_confirmation`
5. `client_confirm`
6. `assigned`

### Ramas laterales

- `client_reject` -> `waiting_provider_response`
- `client_cancel` -> `cancelled`
- `timeout (pending_client_confirmation)` ->
  - `waiting_provider_response`, o
  - `pending_client_decision` (si vence ventana total)

## Invariantes V2 (canónicas)

1. En marketplace, `assigned` solo puede ocurrir despues de confirmacion explicita de cliente.
2. `provider_accept` marketplace no puede llevar directo a `assigned`.
3. Toda salida desde `pending_client_confirmation` limpia `selected_provider_id`.
4. Toda salida desde `pending_client_confirmation` desactiva assignment activo inesperado (defensivo).
5. No hay rutas canónicas de timeout que salten a `assigned`.

## Trazabilidad por capa

Dominio:

- `jobs/services.py`
  - `accept_provider_offer(...)`
  - `accept_marketplace_offer(...)`
  - `confirm_marketplace_provider(...)`
  - `reject_marketplace_provider(...)`
  - `process_marketplace_client_confirmation_timeout(...)`
  - `apply_client_marketplace_decision(...)`

Runtime UI:

- `ui/views.py`
  - acciones de `request_status_view`:
    - `confirm_provider`
    - `reject_provider`
    - `cancel_request`

Interfaz:

- `templates/request/status.html`
  - acciones visibles para `pending_client_confirmation`:
    - Confirm Provider
    - Reject Provider
    - Cancel Request

## Rutas legacy

- `jobs/views.py -> assign_provider` deshabilitada
- respuesta: `400 legacy_assign_provider_endpoint_disabled`

## Evidencia de validacion

Suites focalizadas ejecutadas y en verde:

- `ui.test_marketplace_provider_accept_canonical`
- `ui.test_marketplace_client_confirmation_runtime`
- `jobs.test_marketplace_client_confirmation`
- `jobs.test_marketplace_provider_accept_race`
- `jobs.test_marketplace_client_decision`

Resultado consolidado reportado durante el cierre:

- 21 tests OK
- 17 tests OK

## Nota de trazabilidad (checker adicional)

Se registro un reporte externo tipo checker con "4 problems found" sin detalle tecnico incluido en este hilo.

Estado honesto al cierre:

- runtime y tests focalizados del contrato: OK
- checker adicional: pendientes de detalle para clasificacion

Accion recomendada fuera de este checkpoint:

- correr ese checker con salida detallada y anexar evidencia en checkpoint tecnico complementario.

## No-objetivos

- No cambia contrato urgency/on-demand.
- No redefine ranking/broadcast fuera del contrato de estados marketplace.

## Tag sugerido

- `NODO_MARKETPLACE_OPERATIONAL_CONTRACT_V2_LOCKED`
