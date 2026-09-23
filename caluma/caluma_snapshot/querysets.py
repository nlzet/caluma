from django.db import models


class SnapshotQuerySet(models.QuerySet):
    def for_snapshot(self, snapshot):
        """Select definitions belonging to a snapshot instance or ID."""
        return self.filter(snapshot=snapshot)

    def _prepare_for_bulk_create(self, objs):
        for obj in objs:
            obj.prepare_snapshot_id()
        return super()._prepare_for_bulk_create(objs)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        self._check_identity_fields(update_fields or ())
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def bulk_update(self, objs, fields, batch_size=None):
        self._check_identity_fields(fields)
        return super().bulk_update(objs, fields, batch_size=batch_size)

    def _check_identity_fields(self, fields):
        identity_fields = {"id", "pk", "slug", "snapshot", "snapshot_id"}
        for relation in getattr(self.model, "snapshot_relations", ()):
            identity_fields.update({relation, f"{relation}_id"})
        if identity_fields.intersection(fields):
            raise ValueError("Snapshot identities cannot be changed in place.")

    def update(self, **kwargs):
        self._check_identity_fields(kwargs)
        return super().update(**kwargs)
