from django.db import migrations, models

from caluma.caluma_snapshot.migration_utils import create_initial_snapshot


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Snapshot",
            fields=[
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("modified_at", models.DateTimeField(auto_now=True, db_index=True)),
                (
                    "created_by_user",
                    models.CharField(
                        blank=True, db_index=True, max_length=150, null=True
                    ),
                ),
                (
                    "created_by_group",
                    models.CharField(
                        blank=True, db_index=True, max_length=150, null=True
                    ),
                ),
                (
                    "modified_by_user",
                    models.CharField(
                        blank=True, db_index=True, max_length=150, null=True
                    ),
                ),
                (
                    "modified_by_group",
                    models.CharField(
                        blank=True, db_index=True, max_length=150, null=True
                    ),
                ),
            ],
            options={"abstract": False},
        ),
        migrations.RunPython(create_initial_snapshot, migrations.RunPython.noop),
    ]
