from caluma.caluma_core.factories import DjangoModelFactory

from .models import Snapshot


class SnapshotFactory(DjangoModelFactory):
    class Meta:
        model = Snapshot
