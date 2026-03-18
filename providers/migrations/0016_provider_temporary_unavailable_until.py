from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("providers", "0015_provider_base_dispatch_score"),
    ]

    operations = [
        migrations.AddField(
            model_name="provider",
            name="temporary_unavailable_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
