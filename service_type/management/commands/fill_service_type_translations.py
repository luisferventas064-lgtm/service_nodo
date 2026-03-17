from django.core.management.base import BaseCommand
from django.db import transaction

from service_type.models import ServiceType


TRANSLATIONS = {
    "plumbing": {"en": "Plumbing", "fr": "Plomberie", "es": "Plomería"},
    "cleaning": {"en": "Cleaning", "fr": "Nettoyage", "es": "Limpieza"},
    "electrical": {"en": "Electrical", "fr": "Électricité", "es": "Electricidad"},
    "painting": {"en": "Painting", "fr": "Peinture", "es": "Pintura"},
    "moving": {"en": "Moving", "fr": "Déménagement", "es": "Mudanza"},
    "handyman": {"en": "Handyman", "fr": "Homme à tout faire", "es": "Mantenimiento general"},
    "hvac": {"en": "HVAC", "fr": "CVAC", "es": "Climatización"},
    "landscaping": {"en": "Landscaping", "fr": "Aménagement paysager", "es": "Jardinería"},
    "appliance repair": {"en": "Appliance Repair", "fr": "Réparation d’appareils", "es": "Reparación de electrodomésticos"},
    "pest control": {"en": "Pest Control", "fr": "Extermination", "es": "Control de plagas"},
    "roofing": {"en": "Roofing", "fr": "Toiture", "es": "Techos"},
    "flooring": {"en": "Flooring", "fr": "Revêtement de sol", "es": "Pisos"},
    "window cleaning": {"en": "Window Cleaning", "fr": "Nettoyage de vitres", "es": "Limpieza de ventanas"},
    "carpet cleaning": {"en": "Carpet Cleaning", "fr": "Nettoyage de tapis", "es": "Limpieza de alfombras"},
    "junk removal": {"en": "Junk Removal", "fr": "Débarras", "es": "Retiro de basura"},
    "snow removal": {"en": "Snow Removal", "fr": "Déneigement", "es": "Remoción de nieve"},
    "locksmith": {"en": "Locksmith", "fr": "Serrurerie", "es": "Cerrajería"},
    "home inspection": {"en": "Home Inspection", "fr": "Inspection résidentielle", "es": "Inspección de vivienda"},
    "pressure washing": {"en": "Pressure Washing", "fr": "Lavage à pression", "es": "Lavado a presión"},
    "general repair": {"en": "General Repair", "fr": "Réparation générale", "es": "Reparación general"},
}


def normalize(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


class Command(BaseCommand):
    help = "Fill ServiceType localized fields (name_en, name_fr, name_es) from a predefined mapping."

    def add_arguments(self, parser):
        parser.add_argument(
            "--only-empty",
            action="store_true",
            help="Only fill rows where one or more localized fields are empty.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        only_empty = options["only_empty"]
        updated = 0
        skipped = 0
        missing = []

        for service_type in ServiceType.objects.all().order_by("service_type_id"):
            key = normalize(getattr(service_type, "name", ""))
            data = TRANSLATIONS.get(key)

            if not data:
                missing.append({"service_type_id": service_type.service_type_id, "name": getattr(service_type, "name", "")})
                skipped += 1
                continue

            if only_empty and service_type.name_en and service_type.name_fr and service_type.name_es:
                skipped += 1
                continue

            service_type.name_en = data["en"]
            service_type.name_fr = data["fr"]
            service_type.name_es = data["es"]
            service_type.save(update_fields=["name_en", "name_fr", "name_es"])
            updated += 1

        self.stdout.write(self.style.SUCCESS(f"Updated: {updated}"))
        self.stdout.write(self.style.WARNING(f"Skipped: {skipped}"))

        if missing:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Missing mapping for:"))
            for item in missing:
                self.stdout.write(f'- service_type_id={item["service_type_id"]} name="{item["name"]}"')
