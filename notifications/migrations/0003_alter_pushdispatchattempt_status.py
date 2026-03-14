from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("notifications", "0002_pushdispatchattempt"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pushdispatchattempt",
            name="status",
            field=models.CharField(
                choices=[
                    ("sent", "Sent"),
                    ("stub_sent", "Stub sent"),
                    ("failed", "Failed"),
                ],
                default="stub_sent",
                max_length=20,
            ),
        ),
    ]
