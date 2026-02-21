# jobs/apps.py

from django.apps import AppConfig


class JobsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "jobs"

    def ready(self):
        # Importa signals para registrar los receivers
        from . import signals  # noqa: F401
