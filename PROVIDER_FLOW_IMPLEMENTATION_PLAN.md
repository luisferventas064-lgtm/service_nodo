# Provider Flow Implementation Plan

## Objective
Stabilize the provider profile, services, compliance, and job flow without breaking the parts that already work.

## Canonical Flows To Keep
- Provider profile hub: `templates/providers/profile.html`
- Provider base profile edit: `providers/views.py::provider_edit` + `templates/providers/account.html`
- Provider onboarding checklist: `providers/views.py::provider_complete_profile` + `templates/providers/complete_profile.html`
- Service areas: `providers/views.py::provider_service_areas` + `templates/providers/service_areas.html`
- Insurance: `providers/views.py::provider_insurance` + `templates/providers/insurance.html`
- Certificates: `providers/views.py::provider_certificates` + `templates/providers/certificates.html`
- Provider services: `portal/views.py` + `templates/portal/provider_services.html` and related portal service templates
- Provider jobs: `ui/views.py::provider_jobs_view` + `templates/provider/jobs.html`
- Client request flow: `ui/views.py::request_create_view` + `templates/request/create.html`
- Request status flow: `ui/views.py::request_status_view` + `templates/request/status.html`

## Priority High

### 1. Fix broken or misleading navigation
Files:
- `templates/providers/complete_profile.html`
- `providers/views.py`
- `providers/urls_web.py`

Changes:
- Replace the certificates card link in `templates/providers/complete_profile.html` from `#` to `provider_certificates`.
- Keep `provider_complete_profile` as a checklist page only.
- Make sure every onboarding action points to the real module page:
  - Edit Profile
  - Service Areas
  - Insurance
  - Certificates

Expected result:
- No dead links.
- The onboarding checklist matches the actual screens providers must use.

Risk:
- Low.

### 2. Make one provider dashboard canonical
Files:
- `providers/views.py`
- `portal/views.py`
- `templates/providers/dashboard.html`
- `portal/urls.py`

Changes:
- Choose `portal:provider_dashboard` as the canonical provider dashboard.
- Update redirects after onboarding and login-related provider flow to land on `portal:provider_dashboard`.
- Convert `providers/views.py::provider_dashboard` into a redirect to the portal dashboard, or remove it from navigation.
- Keep `templates/providers/dashboard.html` only if needed as a transitional redirect target; otherwise stop linking to it.

Expected result:
- One dashboard entry point after login, verification, profile completion, and billing completion.

Risk:
- Low.

### 3. Make one provider jobs screen canonical
Files:
- `providers/views.py`
- `ui/views.py`
- `templates/providers/jobs.html`
- `templates/provider/jobs.html`

Changes:
- Keep `ui/views.py::provider_jobs_view` + `templates/provider/jobs.html` as the real jobs flow.
- Replace `providers/views.py::provider_jobs` with a redirect to `ui:provider_jobs`.
- Remove links to `templates/providers/jobs.html`, or convert that route into a redirect-only endpoint.

Expected result:
- Accept, reject, start, and complete actions all happen from a single provider jobs screen.

Risk:
- Low.

### 4. Standardize provider services on the portal flow
Files:
- `providers/views_services.py`
- `providers/urls_web.py`
- `portal/views.py`
- `portal/urls.py`
- `templates/providers/services_list.html`
- `templates/providers/service_form.html`
- `templates/portal/provider_services.html`
- `templates/portal/provider_service_add.html`
- `templates/portal/provider_service_edit.html`

Changes:
- Keep the portal service flow as canonical.
- Stop linking users to the legacy `providers/views_services.py` flow.
- Convert legacy service URLs into redirects to the portal equivalents where possible.
- Preserve existing service data model; do not migrate fields yet.

Expected result:
- One service management UX.
- No duplicated behavior between simple and advanced service forms.

Risk:
- Medium, because route changes can affect existing bookmarks or tests.

### 5. Correct provider profile module navigation
Files:
- `templates/providers/profile.html`

Changes:
- Add a direct link to the canonical provider services page.
- Make sure the profile hub points only to live modules:
  - Edit Profile
  - Manage Service Areas
  - Manage Insurance
  - Manage Certificates
  - Manage Services
  - Compliance

Expected result:
- The profile page becomes a real control panel instead of a partial summary.

Risk:
- Low.

## Priority Medium

### 6. Decide and implement the real billing data owner
Files:
- `providers/models.py`
- `providers/forms.py`
- `providers/views.py`
- `providers/signals.py`
- `templates/providers/complete_billing.html`
- `templates/providers/billing.html`

Current gap:
- `ProviderBillingProfile` exists and is auto-created.
- The billing onboarding form currently saves only address fields on `Provider`.
- Tax and legal billing fields are not exposed in the UI.

Changes:
- Decide whether billing lives in:
  - `Provider` for simple address and operational profile fields
  - `ProviderBillingProfile` for fiscal and legal billing fields
- Recommended split:
  - Keep address fields on `Provider`
  - Move legal/fiscal billing editing to `ProviderBillingProfile`
- Replace `templates/providers/billing.html` placeholder with a real billing profile page.
- Expand `complete_billing` or add a dedicated billing settings screen for:
  - `entity_type`
  - `legal_name`
  - `business_name`
  - `gst_hst_number`
  - `qst_tvq_number`
  - `neq_number`
  - `bn_number`

Expected result:
- Billing completion means something concrete in the data model.
- The UI matches the existing billing-related models.

Risk:
- Medium.

### 7. Clean up stale provider model usage in forms and templates
Files:
- `providers/models.py`
- `providers/forms.py`
- `providers/views.py`
- relevant provider templates

Current gap:
- `Provider.service_area` still exists in the model.
- Actual completion logic uses `ProviderServiceArea`.
- `ProviderIndividualProfileForm` and `ProviderCompanyProfileForm` still mention `service_area`, but the current edit flow does not use these forms.

Changes:
- Mark `Provider.service_area` as legacy in code comments or deprecate it in a later migration.
- Remove or refactor profile forms that still assume `service_area` is the real source of truth.
- Keep profile completion tied only to `ProviderServiceArea`.

Expected result:
- Less confusion for future maintenance.

Risk:
- Medium if migrations are introduced immediately.
- Low if first done as code cleanup only.

### 8. Expose compliance-related provider service fields deliberately
Files:
- `providers/models.py`
- `portal/forms.py`
- `portal/views.py`
- `templates/portal/provider_service_add.html`
- `templates/portal/provider_service_edit.html`

Current gap:
- `ProviderService.compliance_deadline` exists in the model.
- The UI does not show or manage it.

Changes:
- Decide whether providers should set compliance grace periods manually.
- If yes, add `compliance_deadline` to the portal service form.
- If not, keep it hidden and manage it only internally.

Expected result:
- The service form clearly matches the intended business rule.

Risk:
- Medium, because this affects compliance interpretation.

### 9. Add uploads or proof support for certificates if needed
Files:
- `providers/models.py`
- `providers/forms.py`
- `providers/views.py`
- `templates/providers/certificates.html`

Current gap:
- Certificate details are stored.
- There is no file upload or document proof field.

Changes:
- If document proof is required, add a file field or evidence relation.
- If not required yet, keep the current screen but label it clearly as metadata-only.

Expected result:
- Certificates are either intentionally metadata-only or properly evidence-backed.

Risk:
- Medium.

## Priority Low

### 10. Expose Stripe onboarding when ready
Files:
- `providers/models.py`
- `providers/views.py`
- `providers/stripe_services.py`
- `templates/providers/billing.html`
- `templates/providers/compliance.html`

Changes:
- Add UI actions for connected account creation and onboarding link generation.
- Reflect Stripe account status in the billing or compliance screens.

Expected result:
- `billing_profile_completed` can be tied to actual payout readiness later.

Risk:
- Medium to high, but isolated.

### 11. Add zone management only if the business needs it in profile UI
Files:
- `providers/models.py`
- `providers/api.py`
- provider profile templates

Current gap:
- `Provider.zone` and `ServiceZone` exist.
- The provider profile UI does not manage them.

Changes:
- Only add this if zone-level filtering is important for provider operations.
- Otherwise leave zone resolved indirectly through service area and marketplace filters.

Expected result:
- Avoids adding UI complexity before it is needed.

Risk:
- Low.

### 12. Connect workers only after provider-service-job flow is stable
Files:
- `workers/views.py`
- `workers/models.py`
- `assignments/models.py`
- `assignments/services.py`
- any future worker job templates

Current gap:
- Worker onboarding exists.
- Worker is not fully wired into the live provider request flow from the UI.

Changes:
- Add explicit assignment UI only after provider jobs and services are stabilized.
- Then decide whether:
  - provider assigns a worker after accepting a job
  - worker self-claims an assigned provider job
  - worker lifecycle becomes the operational source instead of provider lifecycle

Expected result:
- Worker rollout happens on top of a stable assignment model, not in parallel with provider cleanup.

Risk:
- High if attempted too early.

## Recommended Implementation Order
1. Fix links and route confusion.
2. Make one canonical provider dashboard.
3. Make one canonical provider jobs screen.
4. Make one canonical provider services flow.
5. Add missing service and compliance navigation from the profile hub.
6. Implement real billing ownership and UI.
7. Clean legacy profile/service-area assumptions.
8. Decide on certificate upload support.
9. Add Stripe onboarding.
10. Add worker assignment UI.

## Safe Rule While Implementing
- Redirect old routes before deleting old templates or views.
- Do not change the current request and job lifecycle while cleaning profile screens.
- Treat `Provider`, `ProviderServiceArea`, `ProviderService`, `Job`, and `JobAssignment` as the current live backbone.
