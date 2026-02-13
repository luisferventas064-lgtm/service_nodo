# CHECKPOINT ARCHITECTURE – service_nodo (ACTUAL)

Perfecto.
Aquí te lo paso **limpio, plano, listo para pegar en .txt** junto con tu CHECKPOINT_core.
Sin explicaciones adicionales.

---

CHECKPOINT_ARCHITECTURE – NODO
Estado actual: URGENCIA + NORMAL definidos
Regla: Paso a paso. Un bloque por vez.
Si se modifica modelo → enviar bloque completo.

---

1. FLUJOS DEL SISTEMA

---

🔥 URGENCIA (≤ 48 horas) – Broadcast tipo Uber

Cliente define:

* ServiceType
* Skill(s) (+ “OTRO” si aplica)
* Tiempo:

  * NOW
  * O una hora específica dentro de ahora → +48h

No se muestra precio al inicio.

Sistema envía alerta a:

* Provider Autónomos ONLINE
* ProviderStaff ONLINE (empresas)

Regla:
FIRST ACCEPT WINS

El primero que acepta:
→ Job entra en HOLD (único y universal)

Si alguien rechaza:

* Se excluye solo para ese job
* No se le vuelve a enviar esa urgencia

---

HOLD (URGENCIA)

El job permanece en HOLD hasta que se cumplan:

1. Confirmación del Provider

   * Autónomo: su aceptación ya cuenta
   * Empresa: Admin debe confirmar (máximo 5 minutos)

2. Provider/Admin envía precio final

3. Cliente acepta el precio

Solo cuando:
Provider confirmado + Cliente acepta precio
→ Estado pasa a ASSIGNED

Si cliente NO acepta precio:

* Se libera HOLD
* Se vuelve a buscar otro provider
* El cliente NO puede volver a pedir al mismo provider en ese job
* Se muestra advertencia (puede no encontrar otro o puede ser más caro/barato)

---

Empresa en URGENCIA

ProviderStaff:

* Puede ACEPTAR o RECHAZAR
* Si acepta → HOLD
* Se notifica al Admin
* Admin tiene 5 minutos para confirmar
* Si no confirma → auto-liberación y rebroadcast

---

Liberación de información (Seguridad – Opción B)

Después de confirmación:

Etapa 1:

* Zona aproximada
* Detalle del servicio
* Hora confirmada

Etapa 2 (cuando marca “EN CAMINO”):

* Dirección exacta
* Teléfono del cliente

Cobros automáticos, fee o porcentaje:
NO IMPLEMENTADO AÚN (fase futura)

---

🧾 NORMAL = MARKETPLACE

Marketplace es solo interfaz para crear Jobs normales.

Cliente puede:

* Ver providers por ServiceType
* Filtrar por:

  * km
  * precio por skill
  * skills ofrecidos

Precio mostrado:
Precio del skill seleccionado

Regla:
Solicitud se envía a 1 SOLO provider
(No subasta, no broadcast en normal)

Backend NORMAL:

Estado inicial:
PENDING_PROVIDER_CONFIRMATION

Provider puede:

* Aceptar → ASSIGNED
* Proponer otra hora → PENDING_CLIENT_CONFIRMATION
* Rechazar → cancelar o volver a marketplace

---

2. PRECIOS Y CATÁLOGO

* Sistema define ServiceType
* Provider elige ServiceTypes que trabaja
* Provider define skills y precio por skill
* En Marketplace se muestra precio por skill
* En Urgencia el precio se envía después del HOLD

---

3. ACTORES

Provider:

* Autónomo
* Empresa

ProviderStaff (solo urgencias):

* Perfil mínimo
* ONLINE/OFFLINE
* Ubicación activa
* Acepta / Rechaza

ProviderAdmin:

* Confirma HOLD en urgencias
* Gestiona solicitudes normales

Worker Marketplace:

* Perfil completo
* Flujo separado (no implementado aún)

---

4. ESTADOS DEFINIDOS

POSTED
HOLD
PENDING_PROVIDER_CONFIRMATION
PENDING_CLIENT_CONFIRMATION
ASSIGNED
IN_PROGRESS
COMPLETED
CONFIRMED
CANCELLED
EXPIRED

---

5. NO IMPLEMENTADO AÚN

Pagos automáticos
Comisiones
Emergency fee
Fee por km
Punto de no cancelación
Calendario externo
Ratings

---

..............................................................

---

CHECKPOINT_CORE – NODO
Estado: URGENCIA HOLD + Confirm Provider + Expiración automática
Regla: Paso a paso. Un bloque por vez.
Base estable alineada ORM + SQL.

---

STACK

Python 3.14
Django 5.2.11
SQL Server SQLEXPRESS
ODBC Driver 17
Proyecto: service_nodo

---

BASE_ESTABLE_V3

---

ARQUITECTURA ACTUAL

Modo NORMAL (Marketplace)
Modo URGENCIA (con HOLD transaccional)

Snapshot pricing desacoplado del catálogo dinámico.
Emergency fee configurable.
Redondeo financiero a 2 decimales con ROUND_HALF_UP.
Concurrency protegido con select_for_update.

---

MODELO Job – CAMPOS CLAVE URGENCIA

hold_provider
hold_expires_at

quoted_urgent_total_price
quoted_urgent_fee_amount

Campos snapshot ya existentes:
quoted_base_price
quoted_currency_code
quoted_pricing_unit
quoted_emergency_fee_type
quoted_emergency_fee_value

---

SERVICIOS ACTIVOS

jobs/services_urgent_price.py
compute_urgent_price(job)
→ retorna (urgent_total, urgent_fee_amount)

jobs/services_urgent_hold.py
hold_job_urgent(job_id, provider_id)
→ SELECT FOR UPDATE
→ valida status elegible
→ aplica HOLD
→ congela precio urgente

jobs/services_urgent_confirm.py
confirm_urgent_job(job_id, provider_id)
→ valida HOLD activo
→ valida mismo provider
→ cambia estado (actualmente assigned; mañana se migrará a pending_client_confirmation para doble confirmación)

jobs/services_urgent_hold_expire.py
release_expired_holds()
→ libera HOLD expirados
→ limpia hold_provider
→ limpia hold_expires_at
→ limpia quoted_urgent_total_price
→ limpia quoted_urgent_fee_amount

jobs/management/commands/tick_on_demand.py
→ handle()
→ released = release_expired_holds()
→ imprime NOW
→ imprime RELEASED HOLDS
→ imprime DUE JOBS

---

FLUJO URGENCIA ACTUAL

1. Job en estado posted
2. Provider ejecuta HOLD
3. HOLD bloquea por tiempo (ej. 3 minutos)
4. Precio urgente congelado en Job
5. Provider confirma
6. Estado pasa a assigned
7. HOLD se limpia
8. Tick libera HOLD expirados automáticamente

---

VALIDACIONES IMPLEMENTADAS

No permite HOLD si job_status en:
assigned
in_progress
completed
confirmed
cancelled
expired

No permite confirm si:
no existe HOLD
HOLD expirado
HOLD pertenece a otro provider
precio urgente no está congelado

---

PRUEBAS REALIZADAS

HOLD en job posted → OK
Confirm provider → OK
Status pasa a assigned → OK
Expiración manual → OK
Tick libera HOLD expirado → OK
Concurrency validado previamente

---

ESTADO GLOBAL

URGENCIA MODE → TRANSACCIONAL
HOLD ATÓMICO → ACTIVO
CONFIRM PROVIDER → ACTIVO
EXPIRACIÓN AUTOMÁTICA → ACTIVA
TICK INTEGRADO → ACTIVO
SIN DEUDA TÉCNICA

---

PRÓXIMO PASO (PENDIENTE)

Implementar DOBLE CONFIRMACIÓN:

Provider confirma → pending_client_confirmation
Cliente confirma → assigned

Servicio nuevo requerido:
client_confirm_urgent_job(job_id, client_id)

---

CORE PRINCIPLES

Snapshot pricing obligatorio
Nunca recalcular precio después de HOLD
Siempre usar select_for_update en transiciones críticas
Estados explícitos y controlados
Tick responsable de limpieza automática
Sin dependencia dinámica del catálogo

---

ARQUITECTURA ESTABLE
BASE SEGURA
LISTO PARA CONTINUAR MAÑANA

---

Cuando abras el nuevo chat, pega este archivo completo y escribe:

Continuamos con B: doble confirmación.
