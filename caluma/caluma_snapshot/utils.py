from django.http import Http404
from rest_framework.exceptions import ValidationError


def resolve_snapshot_id(snapshot=None, source=None):
    if snapshot is not None:
        return snapshot
    return source.snapshot_id if source is not None else 1


def visible_instance(queryset, **lookup):
    """Prevent a hidden row from being treated as a new row during an upsert."""
    instance = queryset.filter(**lookup).first()
    if instance is None and queryset.model.objects.filter(**lookup).exists():
        raise Http404(f"No {queryset.model._meta.object_name} matches the given query.")
    return instance


def rebind_definition(definition, snapshot):
    """Find the corresponding definition when copying into another snapshot."""
    if definition is None or definition.snapshot_id == snapshot:
        return definition
    try:
        return type(definition).objects.get(slug=definition.slug, snapshot_id=snapshot)
    except type(definition).DoesNotExist as exc:
        raise ValidationError(
            f"{type(definition).__name__} '{definition.slug}' does not exist "
            f"in snapshot {snapshot}."
        ) from exc


def validate_answer_snapshot(question, form):
    if question.snapshot_id != form.snapshot_id:
        raise ValidationError("Question and document must belong to the same snapshot.")
