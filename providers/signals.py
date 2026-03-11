from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Provider, ProviderBillingProfile, ProviderInvoiceSequence, ProviderMetrics
from .ranking import hydrate_provider_metrics


def _map_entity_type(provider_type: str) -> str:
    # provider_type: "self_employed" | "company"
    if provider_type == "company":
        return ProviderBillingProfile.EntityType.COMPANY
    return ProviderBillingProfile.EntityType.SELF_EMPLOYED


@receiver(post_save, sender=Provider)
def ensure_provider_profiles(sender, instance: Provider, created: bool, **kwargs):
    """
    Create the provider helper objects if they do not exist.
    - Billing profile (1:1)
    - Invoice sequence (1:1) con prefix PROV-{provider_id}-
    Idempotente: si ya existe, no crea duplicado.
    """
    # BillingProfile
    ProviderBillingProfile.objects.get_or_create(
        provider=instance,
        defaults={
            "entity_type": _map_entity_type(instance.provider_type),
        },
    )

    # InvoiceSequence
    ProviderInvoiceSequence.objects.get_or_create(
        provider=instance,
        defaults={
            "prefix": f"PROV-{instance.provider_id}-",
            "next_number": 1,
        },
    )

    metrics, metrics_created = ProviderMetrics.objects.get_or_create(provider=instance)
    if created or metrics_created:
        metrics.jobs_completed = instance.completed_jobs_count or 0
        metrics.jobs_cancelled = instance.cancelled_jobs_count or 0
        metrics.jobs_accepted = max(
            metrics.jobs_accepted or 0,
            metrics.jobs_completed + metrics.jobs_cancelled,
        )
        hydrate_provider_metrics(instance, metrics)
        metrics.save(
            update_fields=[
                "jobs_completed",
                "jobs_accepted",
                "jobs_cancelled",
                "acceptance_rate",
                "completion_rate",
                "experience_score",
                "operational_score",
                "response_score",
                "updated_at",
            ]
        )

    # Si cambian provider_type despues, mantener entity_type alineado.
    ProviderBillingProfile.objects.filter(provider=instance).exclude(
        entity_type=_map_entity_type(instance.provider_type)
    ).update(entity_type=_map_entity_type(instance.provider_type))
