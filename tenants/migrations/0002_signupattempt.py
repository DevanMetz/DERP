from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignupAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("ip", models.GenericIPAddressField(db_index=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"app_label": "tenants"},
        ),
    ]
