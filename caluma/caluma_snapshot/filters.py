from django import forms
from django_filters import Filter

from caluma.caluma_core.filters import (
    GlobalIDFilter,
    GlobalIDMultipleChoiceFilter,
    MetaFilterSet,
)


class SnapshotFilter(Filter):
    field_class = forms.IntegerField


class SnapshotFilterSet(MetaFilterSet):
    snapshot = SnapshotFilter(field_name="snapshot_id", min_value=1)
    id = GlobalIDFilter(field_name="pk")
    ids = GlobalIDMultipleChoiceFilter(field_name="pk")
