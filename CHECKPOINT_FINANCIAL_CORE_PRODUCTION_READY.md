# CHECKPOINT - FINANCIAL CORE PRODUCTION READY

## Fecha
2026-02-26

## 1. Alcance
Este checkpoint certifica que el motor financiero de NODO cumple con principios de:

- Inmutabilidad contable
- Ledger deterministico
- Settlement historico inalterable
- Refund proporcional matematicamente cerrado
- Idempotencia completa de webhooks
- Proteccion contra recalculo retroactivo en produccion

## 2. Invariantes Garantizados
### 2.1 Ticket
- Ticket FINALIZED inmutable
- Lineas inmutables
- `snapshot_hash` SHA-256
- No recalculo posterior

### 2.2 Payment
- PaymentIntent anclado a `ClientTicket.total_cents`
- No cobro sin ticket FINALIZED
- Webhook idempotente (`StripeWebhookEvent` unique)
- Refund no puede exceder total pagado acumulado

### 2.3 Ledger
- Solo un ledger base/final por job
- Ajustes separados (`is_adjustment=True`)
- Refund proporcional basado en ledger original
- Redondeo `ROUND_HALF_UP`
- Residual absorbido en `platform_fee`
- Validacion dura de igualdad matematica

Invariante:

`provider + platform + tax == refund_amount`

### 2.4 Settlement
- Settlement PAID es completamente inmutable
- `generate_weekly_settlements()` no reutiliza settlements inmutables
- No se permite agregar ledger a settlement PAID
- Refund impacta siguiente ciclo (adjustment model)

### 2.5 Rebuild Protection
- `rebuild_platform_ledger_for_job()` bloqueado en produccion
- No permitido si ledger finalizado
- No permitido si existe payment
- No permitido si existe settlement

## 3. Invariante Maestro Global
`Sum(all ledger gross) = Sum(all settlement totals) = Stripe net`

El sistema no permite reescritura historica.

## 4. Estado
Financial Core declarado:

**PRODUCTION-GRADE READY**
