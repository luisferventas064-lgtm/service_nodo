import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException
from django.utils.translation import gettext as _


PHONE_COUNTRY_CHOICES = [
    ("CA", "+1 Canada"),
    ("US", "+1 United States"),
    ("MX", "+52 Mexico"),
    ("OTHER", "Other"),
]

PHONE_COUNTRY_NAMES = {
    "CA": "Canada",
    "US": "United States",
    "MX": "Mexico",
    "OTHER": "Other",
}

SUPPORTED_PHONE_REGIONS = ("CA", "US", "MX")

# ============================================================================
# TEST PHONE UTILITIES (DEBUG MODE ONLY)
# ============================================================================

TEST_PHONE_PREFIX = "+111"  # Reserved prefix for test phones in DEBUG mode


def is_test_phone(phone_number: str) -> bool:
    """Check if a phone number is a test phone (for DEBUG environment only)."""
    from django.conf import settings

    if not settings.DEBUG:
        return False

    phone = str(phone_number or "").strip()
    return phone.startswith(TEST_PHONE_PREFIX)


def generate_test_phone(index: int = 1) -> str:
    """Generate a test phone number.

    Args:
        index: Sequential number (1-based) to create unique test phones

    Returns:
        Test phone in E.164 format: +11100000001, +11100000002, etc.
    """
    return f"{TEST_PHONE_PREFIX}00000{str(index).zfill(3)}"


def is_phone_duplicate_allowed(phone_number: str) -> bool:
    """Check if duplicate phone numbers are allowed.

    In DEBUG mode:
    - Test phones (+111...) can be duplicated (for fixtures)
    - Production phones must be unique

    In PRODUCTION:
    - All phones must be unique
    """
    from django.conf import settings

    if not settings.DEBUG:
        return False

    # Allow duplicates for test phones only
    return is_test_phone(phone_number)


# ============================================================================
# PRODUCTION PHONE UTILITIES
# ============================================================================


def normalize_phone(country_code, phone_local):
    normalized_country = (country_code or "").strip().upper()
    normalized_phone = str(phone_local or "").strip()

    if normalized_country == "OTHER":
        raise ValueError(_("Custom country handling is not available yet."))

    if normalized_country not in SUPPORTED_PHONE_REGIONS:
        raise ValueError(_("Unsupported country."))

    try:
        parsed = phonenumbers.parse(normalized_phone, normalized_country)
    except NumberParseException as exc:
        raise ValueError(_("Invalid phone number.")) from exc

    if not phonenumbers.is_valid_number(parsed):
        raise ValueError(_("Invalid phone number."))

    return phonenumbers.format_number(
        parsed,
        phonenumbers.PhoneNumberFormat.E164,
    )


def best_effort_normalize_phone(value):
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""

    if raw_value.startswith("+"):
        try:
            parsed = phonenumbers.parse(raw_value, None)
        except NumberParseException:
            return raw_value
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed,
                phonenumbers.PhoneNumberFormat.E164,
            )
        return raw_value

    for region in SUPPORTED_PHONE_REGIONS:
        try:
            return normalize_phone(region, raw_value)
        except ValueError:
            continue

    return raw_value


def phone_lookup_candidates(value):
    raw_value = str(value or "").strip()
    if not raw_value:
        return []

    candidates = {raw_value}

    if raw_value.startswith("+"):
        try:
            parsed = phonenumbers.parse(raw_value, None)
        except NumberParseException:
            return list(candidates)
        if phonenumbers.is_valid_number(parsed):
            national_number = str(parsed.national_number)
            candidates.add(
                phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            )
            candidates.add(national_number)
            candidates.add(f"{parsed.country_code}{national_number}")
        return list(candidates)

    for region in SUPPORTED_PHONE_REGIONS:
        try:
            candidate = normalize_phone(region, raw_value)
        except ValueError:
            continue
        candidates.add(candidate)
        try:
            parsed = phonenumbers.parse(raw_value, region)
        except NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            national_number = str(parsed.national_number)
            candidates.add(national_number)
            candidates.add(f"{parsed.country_code}{national_number}")

    return list(candidates)
