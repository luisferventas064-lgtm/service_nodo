from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Provider, ProviderBillingProfile, ProviderInvoiceSequence


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

    # Si cambian provider_type despues, mantener entity_type alineado.
    ProviderBillingProfile.objects.filter(provider=instance).exclude(
        entity_type=_map_entity_type(instance.provider_type)
    ).update(entity_type=_map_entity_type(instance.provider_type))
