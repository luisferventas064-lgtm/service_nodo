# CHECKPOINT MIGRATION NODO

Fecha: 2026-03-14

Estado: ACTIVE

## Confirmed

- provider incoming jobs module -> CONFIRMED
- provider accept / decline interface -> CONFIRMED
- `/provider/jobs/incoming/` -> CONFIRMED
- `/provider/jobs/<id>/accept/` -> CONFIRMED
- `/provider/jobs/<id>/decline/` -> CONFIRMED
- `PROVIDER_DECLINED` event type -> CONFIRMED
- `jobs/migrations/0014_alter_jobevent_event_type_add_provider_declined.py` -> CONFIRMED applied
- `JobProviderExclusion` -> CONFIRMED
- `provider_declined(job_id, provider_id)` persistent rule -> CONFIRMED
- incoming queue exclusion after decline -> CONFIRMED
- matching exclusion after decline -> CONFIRMED
- dispatch exclusion after decline -> CONFIRMED
- push exclusion after decline -> CONFIRMED
- selection-view exclusion after decline -> CONFIRMED
- `jobs/migrations/0015_jobproviderexclusion.py` -> CONFIRMED applied
- backend FCM authentication against Firebase -> CONFIRMED
- `notifications/providers/fcm.py` sends real requests to Firebase -> CONFIRMED
- pre-existing urgent-service compile/import errors -> RESOLVED
- `jobs/services_urgent_client_confirm.py` -> compiles/imports correctly
- `jobs/services_urgent_confirm.py` -> compiles/imports correctly

## Confirmed Behavior

- Provider incoming queue lives in `ui/views_provider.py`
- Incoming queue filters jobs in `waiting_provider_response`
- Incoming queue limits jobs to the targeted provider
- Incoming queue validates service capability and request area
- Incoming queue resolves real timing from `JobEvent.payload_json.service_timing`
- `accept` uses the existing lifecycle service
- `decline` now creates a real timeline event
- `decline` persists a business-rule exclusion in `JobProviderExclusion`
- A provider who declined the same job no longer sees it in incoming queue
- A provider who declined the same job is excluded from matching and dispatch
- A provider who declined the same job does not receive `waiting_provider_response` push again
- Provider selection views exclude providers who already declined the same job
- Legacy provider jobs UI reuses the same accept / decline endpoints
- `send_fcm_push(...)` authenticates successfully against Firebase and receives real API responses
- Failure with `TEST_TOKEN` returns Firebase `INVALID_ARGUMENT`, confirming the provider path is live
- Urgent-service modules compile and import cleanly again in Django

## Still Pending

1. real push validation with app/device token

## Not Yet Confirmed

- functional urgent-flow validation -> NOT CONFIRMED
- push delivery to a real device/app -> NOT CONFIRMED

## Verification

- `python manage.py migrate`
- Applied:
  - `jobs.0014_alter_jobevent_event_type_add_provider_declined`
  - `jobs.0015_jobproviderexclusion`
- `python manage.py test ui.tests --keepdb --noinput`
- Result: `152 OK`
- `python manage.py test notifications.tests.DispatchJobEventPushTests jobs.test_broadcast_attempt.BroadcastAttemptTests jobs.test_tick_marketplace.MarketplaceTickTests jobs.test_tick_on_demand_command.TickOnDemandCommandTest ui.tests.ProviderIncomingJobsViewTests ui.tests.ProviderJobsViewTests ui.tests.MarketplaceSearchViewTests --keepdb --noinput`
- Result: `46 OK`
- `python manage.py check`
- Result: `System check identified no issues (0 silenced)`
- Django FCM probe via `send_fcm_push(token="TEST_TOKEN", payload=...)`
- Result: Firebase responded `400 INVALID_ARGUMENT` for invalid registration token
- `python -m py_compile jobs/services_urgent_client_confirm.py jobs/services_urgent_confirm.py`
- Result: compile OK
- Django import validation for urgent-service modules
- Result: import OK
