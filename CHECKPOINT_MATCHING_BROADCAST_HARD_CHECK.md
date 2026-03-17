# CHECKPOINT MATCHING / BROADCAST -- HARD CHECK

Fecha: 2026-03-16

Estado: REVIEWED / ACTION REQUIRED

## Objetivo

Congelar el mapa real de:

1. entrypoints de seleccion de provider
2. uso real de ranking
3. comportamiento broadcast/reopen/expire
4. split marketplace vs urgency
5. rutas legacy o inconsistentes
6. recomendacion canonica

## 1) EntryPoints reales

### 1.1 Cliente -> marketplace/request

- `marketplace_search_view` en `ui/views.py`
- `providers_nearby_view` en `ui/views.py`
- `request_create_view` en `ui/views.py`

`request_create_view` crea job con provider preseleccionado:

- `selected_provider=provider`
- status inicial:
  - `scheduled` -> `scheduled_pending_activation`
  - `on_demand` (`urgent` / `emergency`) -> `waiting_provider_response`

### 1.2 Provider -> aceptar/rechazar

- `provider_incoming_jobs_view` en `ui/views_provider.py`
- `provider_accept_job_view` / `provider_decline_job_view` en `ui/views_provider.py`
- `provider_job_action_view` en `ui/views.py`

Aceptacion actual usa `accept_job_by_provider` (`jobs/services_lifecycle.py`) y pasa directo a `assigned`.

Rechazo crea exclusion (`JobProviderExclusion`) y deja job en `waiting_provider_response` con `selected_provider=None`.

### 1.3 Tick runners

- `tick_all` ejecuta:
  - `tick_scheduled_activation`
  - `tick_on_demand`
  - `tick_marketplace`

- `tick_on_demand`:
  - procesa jobs `on_demand` `posted` (scheduler/idempotencia)
  - registra `JobBroadcastAttempt`
  - expira `waiting_provider_response` on-demand stale

- `tick_marketplace`:
  - procesa jobs `scheduled` (`posted` o `waiting_provider_response`)
  - envia olas de broadcast
  - maneja timeout de client confirmation

## 2) Source of provider selection

Hay 2 fuentes hoy:

1. preseleccion manual desde UI (request_create con provider_id)
2. seleccion por ranking/broadcast en ticks (`rank_broadcast_candidates_for_job`)

## 3) Ranking usage

### Marketplace list / nearby

- `providers/services_marketplace.py` -> `marketplace_ranked_queryset`
- orden principal por `compliance_score`, `hybrid_score`, `safe_rating`, `price`

### Dispatch / broadcast

- `jobs/services.py` -> `rank_broadcast_candidates_for_job`
- combina:
  - area match
  - cooldown penalty
  - active load penalty
  - geodistance runtime
  - fairness (last_job_assigned_at)
  - random bonus estable por `(job,provider,attempt)`
- ola adaptativa via `select_broadcast_wave_candidates`

## 4) Broadcast / reopen / expire behavior

### Scheduled path (marketplace)

- `process_marketplace_job`:
  - despacha ola (`JobBroadcastAttempt`)
  - mueve a `waiting_provider_response` cuando empieza busqueda
  - expira por max attempts / ventana temporal
  - pasa a `pending_client_decision` tras timeout 24h

- `process_marketplace_client_confirmation_timeout`:
  - `pending_client_confirmation` ->
    - `waiting_provider_response` (reopen)
    - o `pending_client_decision` (si vence ventana total)

### On-demand path

- `process_on_demand_job` requiere `posted` + elegible
- `tick_on_demand` registra intentos broadcast
- `expire_waiting_jobs` expira waiting stale on-demand

## 5) Split marketplace vs urgency

### Marketplace / scheduled

- activacion programada: `scheduled_pending_activation` -> `waiting_provider_response`
- broadcast por olas y decision posterior

### Urgency / on-demand

- `service_timing` urgent/emergency usa `job_mode=on_demand`
- flujo HOLD/confirm separado en:
  - `jobs/services_urgent_hold.py`
  - `jobs/services_urgent_confirm.py`
  - `jobs/services_urgent_hold_expire.py`

## 6) Inconsistencies / legacy paths detectadas

1. Doble camino de aceptacion provider:
   - camino A (activo en UI provider): `accept_job_by_provider` -> `assigned` directo
   - camino B (servicio marketplace): `accept_marketplace_offer` -> `pending_client_confirmation`

2. Servicios marketplace de decision/aceptacion existen pero no estan cableados por views/urls runtime actuales:
   - `accept_marketplace_offer`
   - `confirm_marketplace_provider`
   - `apply_client_marketplace_decision`

3. Ruta legacy expuesta por URL global:
   - `jobs/views.py` -> `assign_provider` (asigna y pasa a assigned)
   Esto compite con la logica de matching/broadcast moderno.

4. Decline provider en flujo actual deja `selected_provider=None` en `waiting_provider_response`.
   Esto depende de que otro proceso retome seleccion; en on-demand actual el tick solo agenda desde `posted`.

## 7) Recomendacion canonica

### Canonical target

1. Unificar aceptacion provider en una sola via:
   - provider acepta oferta -> `pending_client_confirmation`
   - cliente confirma -> `assigned`

2. Desactivar o encapsular rutas legacy publicas:
   - `jobs/assign/...`
   - cualquier assign directo por view sin pasar por servicios canonicos

3. Definir explicitamente la maquina de estados por modo:
   - scheduled (marketplace)
   - on_demand urgency (hold/confirm)

4. Asegurar requeue deterministico tras decline/timeouts:
   - estado fuente unico para nuevo matching
   - no depender de estados intermedios ambiguos

5. Mantener guard de lifecycle y agregar guard especifico de rutas legacy de asignacion directa (siguiente fase).

## Conclusión

El core de ranking/broadcast existe y esta bastante avanzado.
El principal riesgo actual es de consistencia de entrada: hay mas de un camino de aceptacion/asignacion, y no todos convergen en la misma semantica (`pending_client_confirmation` vs `assigned` directo).

Este checkpoint deja congelado el mapa real para ejecutar una migracion controlada al camino canonico unico.
