from django import template

register = template.Library()


@register.filter
def cad(value):
    if value is None:
        return "-"
    return "${:,.2f} CAD".format(value)
