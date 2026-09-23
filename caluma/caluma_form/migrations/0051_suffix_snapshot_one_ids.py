from django.db import migrations

from caluma.caluma_snapshot.migration_utils import (
    check_snapshot_reversal,
    suffix_initial_snapshot_ids,
)


class Migration(migrations.Migration):
    dependencies = [("caluma_form", "0050_snapshot")]

    operations = [
        migrations.RunPython(suffix_initial_snapshot_ids, check_snapshot_reversal)
    ]
