from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("settlements", "0008_ledgeradjustment_settlement"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SettlementPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("executed_at", models.DateTimeField()),
                ("reference", models.CharField(max_length=255)),
                ("amount_cents", models.BigIntegerField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "executed_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="executed_settlement_payments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "settlement",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="settlement_payment",
                        to="settlements.providersettlement",
                    ),
                ),
            ],
            options={
                "db_table": "settlement_payment",
                "ordering": ["-executed_at", "-id"],
            },
        ),
    ]
