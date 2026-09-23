from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from caluma.caluma_core.mutation import UserDefinedPrimaryKeyMixin
from caluma.caluma_core.relay import extract_global_id

from .utils import resolve_snapshot_id, visible_instance


class SnapshotMutationMixin(UserDefinedPrimaryKeyMixin):
    """Keep definition upserts keyed by slug and snapshot, independent of PKs."""

    @classmethod
    def __init_subclass_with_meta__(cls, **options):
        options.setdefault("lookup_field", "slug")
        options.setdefault("lookup_input_kwarg", "slug")
        super().__init_subclass_with_meta__(**options)

    @classmethod
    def get_object(cls, root, info, queryset, **input):
        source = None
        if input.get("snapshot") is None and input.get("source"):
            source = get_object_or_404(queryset, pk=extract_global_id(input["source"]))

        lookup = {
            "slug": input["slug"],
            "snapshot_id": resolve_snapshot_id(input.get("snapshot"), source),
        }
        instance = visible_instance(queryset, **lookup)
        operation = "update" if instance is not None else "create"
        if operation not in cls._meta.model_operations:
            raise ValidationError(
                f"{operation.capitalize()} model operation not allowed."
            )
        return instance
