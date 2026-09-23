from django.db import migrations

from caluma.caluma_snapshot.migration_utils import form_snapshot_operations


class Migration(migrations.Migration):
    dependencies = [
        ("caluma_form", "0049_uuid_v7"),
        ("caluma_workflow", "0035_uuid_v7"),
        ("caluma_snapshot", "0001_initial"),
    ]

    operations = form_snapshot_operations()
