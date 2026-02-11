from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('workers', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='worker',
            name='provider',
        ),
        migrations.AddField(
            model_name='worker',
            name='address_line1',
            field=models.CharField(max_length=255, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='worker',
            name='city',
            field=models.CharField(max_length=100, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='worker',
            name='province',
            field=models.CharField(max_length=100, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='worker',
            name='postal_code',
            field=models.CharField(max_length=20, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='worker',
            name='preferred_language',
            field=models.CharField(max_length=50, null=True, blank=True),
        ),
        migrations.AddField(
            model_name='worker',
            name='availability_mode',
            field=models.CharField(max_length=20, default='manual'),
        ),
        migrations.AddField(
            model_name='worker',
            name='country',
            field=models.CharField(max_length=100, default='Canada'),
        ),
        migrations.AlterField(
            model_name='worker',
            name='email',
            field=models.EmailField(unique=True),
        ),
        migrations.AlterField(
            model_name='worker',
            name='phone_number',
            field=models.CharField(max_length=20, null=True, blank=True),
        ),
    ]
