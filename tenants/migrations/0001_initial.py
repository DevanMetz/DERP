import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="TenantCompany",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("schema_name", models.CharField(max_length=63, unique=True, db_index=True)),
                ("name", models.CharField(max_length=200)),
                ("created_on", models.DateField(auto_now_add=True)),
            ],
            options={"abstract": False},
        ),
        migrations.CreateModel(
            name="Domain",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("domain", models.CharField(max_length=253, unique=True, db_index=True)),
                ("is_primary", models.BooleanField(default=True)),
                (
                    "tenant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="domains",
                        to="tenants.tenantcompany",
                    ),
                ),
            ],
            options={"abstract": False},
        ),
    ]
