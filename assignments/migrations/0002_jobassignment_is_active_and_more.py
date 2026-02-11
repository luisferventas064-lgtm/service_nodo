from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("assignments", "0001_initial"),
    ]

    operations = [
        # 1) Agregar columna is_active como NULL primero (no rompe)
        migrations.RunSQL(
            sql="""
                ALTER TABLE [job_assignment] ADD [is_active] bit NULL;
            """,
            reverse_sql="""
                ALTER TABLE [job_assignment] DROP COLUMN [is_active];
            """,
        ),

        # 2) Backfill: poner 1 a todo lo existente
        migrations.RunSQL(
            sql="""
                UPDATE [job_assignment] SET [is_active] = 1 WHERE [is_active] IS NULL;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),

        # 3) Hacer NOT NULL (ya todos tienen valor)
        migrations.RunSQL(
            sql="""
                ALTER TABLE [job_assignment] ALTER COLUMN [is_active] bit NOT NULL;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),

        # 4) Regla DB en SQL Server: único activo por job (índice único filtrado)
        migrations.RunSQL(
            sql="""
                CREATE UNIQUE INDEX [uq_job_assignment_one_active_per_job]
                ON [job_assignment] ([job_id])
                WHERE [is_active] = 1;
            """,
            reverse_sql="""
                DROP INDEX [uq_job_assignment_one_active_per_job] ON [job_assignment];
            """,
        ),
    ]
