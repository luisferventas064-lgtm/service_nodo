from django import template


register = template.Library()


LANGUAGE_TO_COUNTRY = {
    "fr": "FR",
    "en": "GB",
    "es": "ES",
    "pt": "PT",
    "it": "IT",
    "ru": "RU",
    "ar": "SA",
    "zh-hans": "CN",
    "pa": "IN",
    "vi": "VN",
}


def _flag_from_country_code(country_code: str) -> str:
    if len(country_code) != 2 or not country_code.isalpha():
        return "\U0001F310"

    base = 127397
    return "".join(chr(base + ord(letter.upper())) for letter in country_code)


@register.filter
def language_flag(language_code: str) -> str:
    normalized = str(language_code or "").strip().lower().replace("_", "-")
    country_code = LANGUAGE_TO_COUNTRY.get(normalized)

    if country_code is None and "-" in normalized:
        country_code = LANGUAGE_TO_COUNTRY.get(normalized.split("-", 1)[0])

    if country_code is None:
        return "\U0001F310"

    return _flag_from_country_code(country_code)
