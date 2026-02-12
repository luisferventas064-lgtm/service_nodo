from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("providers", "0006_remove_providerservicetype_state_only"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
IF OBJECT_ID(N'provider_service_type', N'U') IS NULL
   AND OBJECT_ID(N'provider_service_area', N'U') IS NOT NULL
   AND EXISTS (
       SELECT 1
       FROM INFORMATION_SCHEMA.COLUMNS
       WHERE TABLE_NAME = 'provider_service_area'
         AND COLUMN_NAME = 'provider_service_type_id'
   )
BEGIN
    EXEC sp_rename 'provider_service_area', 'provider_service_type';
END;

IF OBJECT_ID(N'provider_service_area', N'U') IS NULL
   AND OBJECT_ID(N'providers_providerservicearea', N'U') IS NOT NULL
BEGIN
    EXEC sp_rename 'providers_providerservicearea', 'provider_service_area';
END;

IF OBJECT_ID(N'provider_service_area', N'U') IS NOT NULL
   AND EXISTS (
       SELECT 1
       FROM INFORMATION_SCHEMA.COLUMNS
       WHERE TABLE_NAME = 'provider_service_area'
         AND COLUMN_NAME = 'id'
   )
   AND NOT EXISTS (
       SELECT 1
       FROM INFORMATION_SCHEMA.COLUMNS
       WHERE TABLE_NAME = 'provider_service_area'
         AND COLUMN_NAME = 'provider_service_area_id'
   )
BEGIN
    EXEC sp_rename 'provider_service_area.id', 'provider_service_area_id', 'COLUMN';
END;
""",
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
