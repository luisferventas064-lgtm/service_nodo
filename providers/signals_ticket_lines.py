from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from providers.models import ProviderTicketLine
from providers.totals import recalc_provider_ticket_totals


def _should_recalc(ticket) -> bool:
    return getattr(ticket, "status", "") == "open"


@receiver(post_save, sender=ProviderTicketLine)
def provider_ticket_line_saved(sender, instance: ProviderTicketLine, **kwargs):
    if _should_recalc(instance.ticket):
        recalc_provider_ticket_totals(instance.ticket_id)


@receiver(post_delete, sender=ProviderTicketLine)
def provider_ticket_line_deleted(sender, instance: ProviderTicketLine, **kwargs):
    if _should_recalc(instance.ticket):
        recalc_provider_ticket_totals(instance.ticket_id)
