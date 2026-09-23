from django.conf import settings
from django.db import models
from django.db.models.functions import Cast, Concat

from caluma.caluma_core.models import BaseModel, HistoricalModel

from .querysets import SnapshotQuerySet


class Snapshot(BaseModel):
    id = models.AutoField(primary_key=True)


class SnapshotModel(BaseModel, HistoricalModel):
    """A definition identified by its slug and snapshot.

    New definitions derive their primary key from slug and snapshot. Saved
    definitions keep their original primary key, including migrated rows.
    """

    # Natural-key slugs can contain two 127-character slugs and a separator.
    # Leave room for the snapshot separator and a numeric snapshot ID as well.
    id = models.CharField(max_length=276, primary_key=True, editable=False)
    slug = models.SlugField(max_length=127)
    snapshot = models.ForeignKey(
        Snapshot, on_delete=models.PROTECT, default=1, related_name="+"
    )

    objects = SnapshotQuerySet.as_manager()
    snapshot_relations = ()

    def prepare_snapshot_id(self):
        if self.snapshot_id is None:
            raise ValueError("A snapshot is required for a definition.")

        if self._state.adding:
            self.pk = (
                f"{self.slug}:{self.snapshot_id}"
                if self.snapshot_id != 1 or settings.CALUMA_SNAPSHOT_V1_SUFFIX
                else self.slug
            )
        elif self.pk != f"{self.slug}:{self.snapshot_id}" and not (
            self.snapshot_id == 1 and self.pk == self.slug
        ):
            raise ValueError("Snapshot identities cannot be changed in place.")

        for relation in self.snapshot_relations:
            related = getattr(self, relation)
            if related is not None and related.snapshot_id != self.snapshot_id:
                raise ValueError(
                    "Related definitions must belong to the same snapshot."
                )

    def save(self, *args, **kwargs):
        self.prepare_snapshot_id()
        return super().save(*args, **kwargs)

    def __repr__(self):
        identity = f"slug={self.slug}"
        if self.pk != self.slug:
            identity += f", snapshot_id={self.snapshot_id}"
        return f"{self.__class__.__name__}({identity})"

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["slug", "snapshot"],
                name="%(app_label)s_%(class)s_slug_snapshot_uniq",
            ),
            models.CheckConstraint(
                condition=models.Q(snapshot_id=1, id=models.F("slug"))
                | models.Q(
                    id=Concat(
                        "slug",
                        models.Value(":"),
                        Cast("snapshot_id", output_field=models.CharField()),
                    )
                ),
                name="%(app_label)s_%(class)s_snapshot_id_valid",
            ),
        ]


class SnapshotNaturalKeyModel(SnapshotModel):
    """A membership whose slug is derived from definitions in one snapshot."""

    slug = models.CharField(max_length=255, editable=False)
    snapshot = models.ForeignKey(Snapshot, on_delete=models.PROTECT, related_name="+")
    snapshot_relations = ()

    def natural_key(self):
        return ".".join(
            getattr(self, relation).slug for relation in self.snapshot_relations
        )

    def __repr__(self):
        if self.pk == self.slug:
            return f"{self.__class__.__name__}(id={self.pk})"
        return super().__repr__()

    def prepare_snapshot_id(self):
        snapshots = {
            getattr(self, relation).snapshot_id for relation in self.snapshot_relations
        }
        if self.snapshot_id is None and len(snapshots) == 1:
            self.snapshot_id = next(iter(snapshots))
        if snapshots != {self.snapshot_id}:
            raise ValueError("Related definitions must belong to the same snapshot.")

        self.slug = self.natural_key()
        super().prepare_snapshot_id()

    class Meta(SnapshotModel.Meta):
        abstract = True
