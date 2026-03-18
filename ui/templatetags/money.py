from django import template
from django.utils import formats
from django.utils.translation import get_language

register = template.Library()


@register.filter
def cad(value):
    if value is None:
        return "-"
    normalized = formats.number_format(value, decimal_pos=2, use_l10n=True)
    language = (get_language() or "").lower()
    if language.startswith("fr"):
        return f"{normalized} $"
    return f"${normalized}"
