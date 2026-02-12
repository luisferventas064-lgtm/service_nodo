from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("providers", "0005_remove_providerservicearea_id_and_more"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(
                    name="ProviderServiceType",
                ),
            ],
        ),
    ]
# “Aplicada para resolver desfase temporal;
#  luego 0007 y cambios posteriores reintroducen ProviderServiceType”.