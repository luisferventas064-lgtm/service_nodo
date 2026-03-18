# CHECKPOINT_PROVIDER_AVAILABILITY_EXISTING_STATE

## 1) Campos existentes (estado actual)

### Provider
- availability_mode (manual/auto)
- is_available_now (boolean)
- temporary_unavailable_until (datetime nullable)
- service_radius_km
- is_active
- restricted_until (calidad/restriccion)
- last_job_assigned_at

### Worker
- availability_mode
- is_available_now

### Job / Assignment relacionados
- JobProviderExclusion (job + provider, unique)
- JobAssignment.is_active
- Job statuses operativos incluyen waiting_provider_response, scheduled_pending_activation, assigned, pending_client_confirmation, in_progress

### No encontrados (intencionalmente fuera de scope por ahora)
- accepts_scheduled
- accepts_urgent
- working_hours
- calendar
- max_active_jobs por provider (solo existe global)

## 2) Availability minimum contract (ACTIVE)

Provider es elegible operativamente solo si:
1. is_available_now = True
2. availability_mode != "paused"
3. temporary_unavailable_until es null o <= now

Aplicado como filtro duro en:
- marketplace candidate ranking (matching)
- provider incoming eligibility

Cobertura de tests activa:
- unavailable provider excluded
- temporary pause active excluded
- expired temporary pause allowed
- incoming hidden during temporary pause
- suites de regresion existentes en verde

## 3) Logica existente (congelada)

### Matching/ranking
- Filtra Provider.is_active
- Exige ProviderService activo para service_type
- Filtra por area de servicio
- Excluye por JobProviderExclusion
- Aplica capacidad con MAX_ACTIVE_JOBS global
- Agrega cooldown_penalty y load_penalty
- Ahora aplica availability minimum contract como filtro duro

### Incoming provider
- Fuente base: waiting_provider_response con selected_provider
- Valida pricing snapshot, area, exclusion
- Ahora aplica availability minimum contract como filtro duro en elegibilidad

### Decline
- Decline incoming: crea JobProviderExclusion, limpia selected_provider y recicla
- Decline scheduled_pending_activation: crea exclusion, limpia selected_provider, cancela assignment activo, vuelve a waiting_provider_response

## 4) Segmentacion portal provider (CONTRACT FREEZE)

### Incoming
- Ruta: ui:provider_incoming_jobs
- Significado: ofertas nuevas sobre waiting_provider_response para provider seleccionado
- Reglas: elegibilidad estricta (pricing/area/exclusion + availability minimum contract)

### Missions
- Estado: NO existe pantalla independiente llamada Missions en portal provider actual
- Contrato temporal: queda pendiente de definicion funcional/producto

### Board
- Ruta: provider_jobs (redirige a ui:provider_jobs)
- Significado: trabajo activo operativo del provider
- Queryset congelado (estado actual):
  - waiting_provider_response
  - scheduled_pending_activation
  - assigned
  - pending_client_confirmation
  - in_progress

### Activity
- Ruta: provider_activity
- Significado: historial filtrable/exportable usando jobs.activity_service
- No es tablero operativo ni cola de ofertas

## 5) Que NO duplicar

1. No crear otro mecanismo de exclusion por job/provider: ya existe JobProviderExclusion.
2. No duplicar logica de carga activa: ya existe filtro por active assignments + MAX_ACTIVE_JOBS.
3. No reinventar decline scheduled: ya esta implementado y probado.
4. No introducir modelo paralelo de disponibilidad basica fuera de is_available_now / availability_mode / temporary_unavailable_until.
5. No mezclar semantica de Incoming/Board/Activity.

## 6) Gaps reales (para fases posteriores)

1. Flags de producto para disponibilidad por tipo (accepts_scheduled / accepts_urgent).
2. Horarios y calendario (working_hours/calendar) con timezone y ventanas.
3. Capacidad por provider (en lugar de solo constante global).
4. Definir y materializar pantalla Missions si producto lo requiere.

## 7) Siguiente bloque recomendado

1. Congelar contrato de segmentacion provider en docs funcionales y tests de comportamiento por vista.
2. Verificar que cada vista mantenga semantica estable:
   - Incoming = ofertas elegibles
   - Board = ejecucion activa
   - Activity = historial
3. Recién despues evaluar flags de producto (accepts_scheduled / accepts_urgent).
