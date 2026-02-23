from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from clients.models import ClientTicketLine
from clients.totals import recalc_client_ticket_totals


def _should_recalc(ticket) -> bool:
    return getattr(ticket, "status", "") == "open"


@receiver(post_save, sender=ClientTicketLine)
def client_ticket_line_saved(sender, instance: ClientTicketLine, **kwargs):
    if _should_recalc(instance.ticket):
        recalc_client_ticket_totals(instance.ticket_id)


@receiver(post_delete, sender=ClientTicketLine)
def client_ticket_line_deleted(sender, instance: ClientTicketLine, **kwargs):
    if _should_recalc(instance.ticket):
        recalc_client_ticket_totals(instance.ticket_id)
