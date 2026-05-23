import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0002_signupattempt"),
    ]

    operations = [
        migrations.CreateModel(
            name="PendingTenant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.UUIDField(default=uuid.uuid4, unique=True, db_index=True)),
                ("company_name", models.CharField(max_length=200)),
                ("subdomain", models.SlugField(max_length=63)),
                ("email", models.EmailField()),
                ("password_hash", models.CharField(max_length=256)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"app_label": "tenants"},
        ),
    ]
