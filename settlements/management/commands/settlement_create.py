from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.utils.dateparse import parse_datetime

from providers.models import Provider
from settlements.services import create_provider_settlement_for_period


class Command(BaseCommand):
    help = "Create provider settlement for a specific period"

    def add_arguments(self, parser):
        parser.add_argument("--provider_id", type=int, required=True)
        parser.add_argument("--start", type=str, required=True)
        parser.add_argument("--end", type=str, required=True)
        parser.add_argument("--currency", type=str, default="CAD")

    def handle(self, *args, **options):
        provider_id = options["provider_id"]
        start = parse_datetime(options["start"])
        end = parse_datetime(options["end"])
        currency = options["currency"]

        if not start or not end:
            self.stderr.write("Invalid datetime format. Use ISO format.")
            return

        try:
            provider = Provider.objects.get(pk=provider_id)

            settlement = create_provider_settlement_for_period(
                provider=provider,
                start=start,
                end=end,
                currency=currency,
            )

            self.stdout.write(
                self.style.SUCCESS(
                    f"Settlement created: ID={settlement.id} "
                    f"Provider={provider_id} "
                    f"Jobs={settlement.total_jobs} "
                    f"Net={settlement.total_net_provider_cents} {currency}"
                )
            )

        except Provider.DoesNotExist:
            self.stderr.write("Provider not found")

        except ValidationError as e:
            self.stderr.write(f"ValidationError: {e}")

        except Exception as e:
            self.stderr.write(f"Error: {str(e)}")
