import re
from html import unescape

from django.contrib.auth import get_user_model
from django.test import Client as DjangoClient
from django.test.utils import override_settings
from django.utils.translation import activate

from clients.models import Client as ClientProfile
from providers.models import Provider as ProviderProfile


User = get_user_model()
auth_user = User.objects.order_by("id").first()
client_profile = ClientProfile.objects.order_by("client_id").first()
provider_profile = ProviderProfile.objects.order_by("provider_id").first()

if not auth_user:
    print("ERROR: No existe ningun usuario auth para login de prueba.")
    raise SystemExit(1)

if not client_profile:
    print("ERROR: No existe ningun perfil client para probar rutas client.")
    raise SystemExit(1)

if not provider_profile:
    print("ERROR: No existe ningun perfil provider para probar rutas provider.")
    raise SystemExit(1)


ROUTES = [
    ("public", "/providers/register/"),
    ("provider", "/verify-phone/"),
    ("provider", "/portal/provider/dashboard/"),
    ("provider", "/providers/missions/"),
    ("provider", "/providers/activity/"),
    ("provider", "/providers/financial-summary/"),
    ("provider", "/providers/compliance/"),
    ("provider", "/providers/profile/"),
    ("provider", "/providers/account/"),
    ("provider", "/providers/insurance/"),
    ("provider", "/providers/certificates/"),
    ("provider", "/provider/jobs/"),
    ("provider", "/provider/jobs/incoming/"),
]

ENGLISH_MARKERS = [
    "Submit",
    "Cancel",
    "Save",
    "Next",
    "Back",
    "Enter",
    "Phone",
    "Email",
    "Password",
    "Continue",
    "Verify",
    "Required",
    "Available",
    "Not available",
    "Monthly breakdown",
    "Settings",
    "Activity History",
    "Financial Summary",
    "Complete Profile",
    "Complete your profile",
]


def build_client_for_context(context_name: str) -> DjangoClient:
    c = DjangoClient()
    c.force_login(auth_user)
    session = c.session

    if context_name == "client":
        session["nodo_role"] = "client"
        session["nodo_profile_id"] = int(client_profile.client_id)
        session["client_id"] = int(client_profile.client_id)
        session.pop("provider_id", None)
        session.pop("worker_id", None)
    elif context_name == "provider":
        session["nodo_role"] = "provider"
        session["nodo_profile_id"] = int(provider_profile.provider_id)
        session["provider_id"] = int(provider_profile.provider_id)
        session.pop("client_id", None)
        session.pop("worker_id", None)
    else:
        session.pop("nodo_role", None)
        session.pop("nodo_profile_id", None)
        session.pop("client_id", None)
        session.pop("provider_id", None)
        session.pop("worker_id", None)

    session.save()
    return c


def visible_text(html: str) -> str:
    cleaned = re.sub(r"<script.*?>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style.*?>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def extract_hits(html: str):
    text = visible_text(html)
    hits = []
    for marker in ENGLISH_MARKERS:
        pattern = r"\b" + re.escape(marker) + r"\b"
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits.append(marker)
    return sorted(set(hits))


activate("fr")

print("\n=== CHECK FR START ===\n")
print(
    "Usuario auth usado: "
    f"{getattr(auth_user, 'username', None) or getattr(auth_user, 'email', None) or auth_user.pk}"
)
print(f"Client profile: {client_profile.client_id} ({client_profile.phone_number})")
print(f"Provider profile: {provider_profile.provider_id} ({provider_profile.phone_number})\n")

with override_settings(ALLOWED_HOSTS=["testserver", "127.0.0.1", "localhost"]):
    for context_name, url in ROUTES:
        try:
            c = build_client_for_context(context_name)
            response = c.get(
                url,
                HTTP_ACCEPT_LANGUAGE="fr",
                HTTP_HOST="127.0.0.1",
            )
            status = response.status_code
            content = response.content.decode("utf-8", errors="ignore")
            hits = extract_hits(content)

            prefix = f"[{context_name.upper()}]"
            if status in (301, 302):
                print(f"{prefix} [REDIRECT] {url} -> {response.get('Location')}")
            elif status >= 400:
                print(f"{prefix} [ERROR_STATUS] {url} -> HTTP {status}")
            elif hits:
                print(f"{prefix} [MEZCLA] {url}")
                print(f"  Ingles detectado: {hits}")
            else:
                print(f"{prefix} [OK] {url}")
        except Exception as exc:
            print(f"[{context_name.upper()}] [ERROR] {url} -> {exc}")

print("\n=== CHECK FR END ===")
