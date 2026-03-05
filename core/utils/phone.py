import phonenumbers
from phonenumbers.phonenumberutil import NumberParseException


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


def normalize_phone(country_code, phone_local):
    normalized_country = (country_code or "").strip().upper()
    normalized_phone = str(phone_local or "").strip()

    if normalized_country == "OTHER":
        raise ValueError("Custom country handling is not available yet.")

    if normalized_country not in SUPPORTED_PHONE_REGIONS:
        raise ValueError("Unsupported country.")

    try:
        parsed = phonenumbers.parse(normalized_phone, normalized_country)
    except NumberParseException as exc:
        raise ValueError("Invalid phone number.") from exc

    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Invalid phone number.")

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
