# jobs/signals.py

from decimal import Decimal
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Job, JobFinancial


@receiver(post_save, sender=Job)
def ensure_job_financial_exists(sender, instance: Job, created: bool, **kwargs):
    # Si no se creó el Job, no hacemos nada (evita trabajo extra en updates)
    if not created:
        return

    JobFinancial.objects.create(
        job=instance,
        base_amount=Decimal("0.00"),
        adjustment_amount=Decimal("0.00"),
        final_amount=Decimal("0.00"),
    )
