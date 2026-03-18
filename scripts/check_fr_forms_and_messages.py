import re
from html import unescape

from django.contrib.auth import get_user_model
from django.test import Client
from django.test.utils import override_settings
from django.utils.translation import activate

from clients.models import Client as ClientProfile
from providers.models import Provider as ProviderProfile


def visible_text(html: str) -> str:
    cleaned = re.sub(r"<script.*?>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<style.*?>.*?</style>", " ", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


EN_MARKERS = [
    "This field is required",
    "Enter a valid email",
    "Invalid phone number",
    "Passwords do not match",
    "Please log in",
    "Invalid verification code",
    "Code expired",
    "Verification code not found",
    "Invalid payload",
    "Too many requests",
    "No data",
]


FR_HINTS = [
    "Ce champ",
    "obligatoire",
    "invalide",
    "mot de passe",
    "verification",
    "connexion",
]


def extract_hits(text: str, markers: list[str]) -> list[str]:
    hits = []
    for marker in markers:
        if re.search(re.escape(marker), text, flags=re.IGNORECASE):
            hits.append(marker)
    return sorted(set(hits))


def print_result(label: str, response):
    text = visible_text(response.content.decode("utf-8", errors="ignore"))
    en_hits = extract_hits(text, EN_MARKERS)
    fr_hits = extract_hits(text, FR_HINTS)

    status = response.status_code
    if status in (301, 302):
        print(f"[REDIRECT] {label} -> {response.get('Location')}")
        return

    if en_hits:
        print(f"[MEZCLA] {label} (HTTP {status})")
        print(f"  EN detectado: {en_hits}")
    else:
        print(f"[OK] {label} (HTTP {status})")

    if fr_hits:
        print(f"  FR hints: {fr_hits}")


activate("fr")

User = get_user_model()
auth_user = User.objects.order_by("id").first()
client_profile = ClientProfile.objects.order_by("client_id").first()
provider_profile = ProviderProfile.objects.order_by("provider_id").first()

if not auth_user:
    print("ERROR: No auth user found.")
    raise SystemExit(1)

if not client_profile or not provider_profile:
    print("ERROR: Missing client/provider profiles for session checks.")
    raise SystemExit(1)

print("\n=== FR FORMS + MESSAGES CHECK START ===\n")

with override_settings(ALLOWED_HOSTS=["testserver", "127.0.0.1", "localhost"]):
    c = Client()
    c.force_login(auth_user)

    # 1) Client register form required errors
    r = c.post(
        "/clients/register/",
        data={"full_name": "", "email": "", "country": "CA", "phone_local": "", "password": "", "confirm_password": ""},
        HTTP_ACCEPT_LANGUAGE="fr",
        HTTP_HOST="127.0.0.1",
    )
    print_result("client_register_required", r)

    # 2) Client register invalid email/phone mismatch
    r = c.post(
        "/clients/register/",
        data={
            "full_name": "Test User",
            "email": "not-an-email",
            "country": "CA",
            "phone_local": "123",
            "password": "abc12345",
            "confirm_password": "abc12346",
        },
        HTTP_ACCEPT_LANGUAGE="fr",
        HTTP_HOST="127.0.0.1",
    )
    print_result("client_register_invalid_fields", r)

    # 3) Provider register required errors
    r = c.post(
        "/providers/register/",
        data={"business_name": "", "email": "", "country": "CA", "phone_local": "", "password": "", "confirm_password": "", "provider_type": ""},
        HTTP_ACCEPT_LANGUAGE="fr",
        HTTP_HOST="127.0.0.1",
    )
    print_result("provider_register_required", r)

    # 4) Provider register invalid email/phone/password mismatch
    r = c.post(
        "/providers/register/",
        data={
            "business_name": "Test Biz",
            "email": "bad-email",
            "country": "CA",
            "phone_local": "12",
            "password": "abc12345",
            "confirm_password": "abc99999",
            "provider_type": "individual",
        },
        HTTP_ACCEPT_LANGUAGE="fr",
        HTTP_HOST="127.0.0.1",
    )
    print_result("provider_register_invalid_fields", r)

    # 5) verify-phone dynamic error with proper session context
    s = c.session
    s["verify_phone"] = client_profile.phone_number
    s["verify_role"] = "client"
    s["verify_actor_type"] = "client"
    s["verify_actor_id"] = int(client_profile.client_id)
    s["nodo_role"] = "client"
    s["nodo_profile_id"] = int(client_profile.client_id)
    s["client_id"] = int(client_profile.client_id)
    s.save()

    r = c.post(
        "/verify-phone/",
        data={"code": "000000"},
        HTTP_ACCEPT_LANGUAGE="fr",
        HTTP_HOST="127.0.0.1",
    )
    print_result("verify_phone_wrong_code", r)

print("\n=== FR FORMS + MESSAGES CHECK END ===")
