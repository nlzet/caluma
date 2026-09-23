from rest_framework import serializers

from caluma.caluma_core.serializers import ModelSerializer

from .models import Snapshot
from .utils import resolve_snapshot_id


class SnapshotModelSerializer(ModelSerializer):
    snapshot = serializers.IntegerField(
        source="snapshot_id", min_value=1, required=False
    )

    def validate_snapshot(self, value):
        if not Snapshot.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Snapshot does not exist.")
        return value

    def validate(self, data):
        source = data.get("source")
        snapshot = data.setdefault(
            "snapshot_id", resolve_snapshot_id(source=self.instance or source)
        )
        for field in (*getattr(self.Meta.model, "snapshot_relations", ()), "options"):
            value = data.get(field)
            definitions = value if isinstance(value, list) else [value]
            if any(obj and obj.snapshot_id != snapshot for obj in definitions):
                raise serializers.ValidationError(
                    {field: "Related definitions must belong to the same snapshot."}
                )
        return super().validate(data)
