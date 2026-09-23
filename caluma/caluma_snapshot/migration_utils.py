"""Migration helpers for introducing snapshot identities.

These helpers are imported by historical migrations. Keep their behavior stable
when adding helpers for later changes.
"""

import re
from functools import partial

from django.conf import settings
from django.core.management.color import no_style
from django.db import migrations, models
from django.db.migrations.exceptions import IrreversibleError
from django.db.models.functions import Cast, Concat


def create_initial_snapshot(apps, schema_editor):
    snapshot = apps.get_model("caluma_snapshot", "Snapshot")
    snapshot.objects.using(schema_editor.connection.alias).create(pk=1)

    # An explicit ID does not advance PostgreSQL's auto-increment sequence.
    for statement in schema_editor.connection.ops.sequence_reset_sql(
        no_style(), [snapshot]
    ):
        schema_editor.execute(statement)


def populate_snapshot_slugs(apps, schema_editor):
    for model_name in ("Form", "Question", "Option", "FormQuestion", "QuestionOption"):
        for prefix in ("", "Historical"):
            model = apps.get_model("caluma_form", f"{prefix}{model_name}")
            model.objects.using(schema_editor.connection.alias).update(
                slug=models.F("id")
            )


def _suffix_analytics_source(path, form_slugs, question_types):
    in_form = False
    result = []
    for part in path.split("."):
        form_reference = re.fullmatch(r"(document|answers)\[([^]]+)\]", part)
        if form_reference:
            field, slug = form_reference.groups()
            if slug in form_slugs:
                part = f"{field}[{slug}:1]"
            in_form = True
        elif in_form:
            if part == "caluma_form":
                in_form = False
            elif part in question_types:
                question_type = question_types[part]
                part = f"{part}:1"
                in_form = question_type in {"form", "table"}
            else:
                in_form = False
        result.append(part)
    return ".".join(result)


def _rewrite_analytics_sources(connection, existing_tables, form_slugs, question_types):
    if "caluma_analytics_analyticsfield" not in existing_tables:
        return

    with connection.cursor() as cursor:
        cursor.execute("SELECT id, data_source FROM caluma_analytics_analyticsfield")
        for field_id, data_source in cursor.fetchall():
            new_source = _suffix_analytics_source(
                data_source, form_slugs, question_types
            )
            if new_source != data_source:
                cursor.execute(
                    "UPDATE caluma_analytics_analyticsfield "
                    "SET data_source = %s WHERE id = %s",
                    [new_source, field_id],
                )


def suffix_initial_snapshot_ids(apps, schema_editor):
    """Rekey imported definitions and every stored FK when v1 uses suffixes."""
    if not settings.CALUMA_SNAPSHOT_V1_SUFFIX:
        return

    model_names = ("Form", "Question", "Option", "FormQuestion", "QuestionOption")
    definitions = [
        apps.get_model("caluma_form", f"{prefix}{name}")
        for name in model_names
        for prefix in ("", "Historical")
    ]
    target_tables = {
        apps.get_model("caluma_form", name)._meta.db_table for name in model_names
    }
    connection = schema_editor.connection
    form_slugs = set(
        apps.get_model("caluma_form", "Form")
        .objects.filter(snapshot_id=1)
        .using(connection.alias)
        .values_list("slug", flat=True)
    )
    question_types = dict(
        apps.get_model("caluma_form", "Question")
        .objects.filter(snapshot_id=1)
        .using(connection.alias)
        .values_list("slug", "type")
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_schema()")
        schema = cursor.fetchone()[0]
        existing_tables = set(connection.introspection.table_names(cursor))
        cursor.execute(
            """
            SELECT source_ns.nspname, source.relname, source_column.attname,
                   fk.condeferrable
            FROM pg_constraint AS fk
            JOIN pg_class AS source ON source.oid = fk.conrelid
            JOIN pg_namespace AS source_ns ON source_ns.oid = source.relnamespace
            JOIN pg_class AS target ON target.oid = fk.confrelid
            JOIN pg_namespace AS target_ns ON target_ns.oid = target.relnamespace
            JOIN pg_attribute AS source_column
              ON source_column.attrelid = source.oid
             AND source_column.attnum = fk.conkey[1]
            JOIN pg_attribute AS target_column
              ON target_column.attrelid = target.oid
             AND target_column.attnum = fk.confkey[1]
            WHERE fk.contype = 'f'
              AND array_length(fk.conkey, 1) = 1
              AND target_ns.nspname = %s
              AND target.relname = ANY(%s)
              AND target_column.attname = 'id'
            """,
            [schema, list(target_tables)],
        )
        database_references = cursor.fetchall()

    if any(not deferrable for _, _, _, deferrable in database_references):
        raise RuntimeError("Snapshot rekeying requires deferrable foreign keys.")

    reference_columns = {
        (table_schema, table, column)
        for table_schema, table, column, _ in database_references
    }
    for model in apps.get_models(include_auto_created=True):
        if model._meta.db_table not in existing_tables:
            continue
        for field in model._meta.concrete_fields:
            if (field.many_to_one or field.one_to_one) and (
                field.remote_field.model._meta.db_table in target_tables
                and field.target_field.primary_key
            ):
                reference_columns.add((schema, model._meta.db_table, field.column))

    quote = schema_editor.quote_name
    schema_editor.execute("SET CONSTRAINTS ALL DEFERRED")
    for table_schema, table, column in sorted(reference_columns):
        qualified_table = f"{quote(table_schema)}.{quote(table)}"
        quoted_column = quote(column)
        schema_editor.execute(
            f"UPDATE {qualified_table} SET {quoted_column} = "
            f"{quoted_column} || ':1' WHERE {quoted_column} IS NOT NULL "
            f"AND position(':' in {quoted_column}) = 0"
        )

    for model in definitions:
        qualified_table = f"{quote(schema)}.{quote(model._meta.db_table)}"
        schema_editor.execute(
            f"UPDATE {qualified_table} SET {quote('id')} = {quote('id')} || ':1' "
            f"WHERE {quote('snapshot_id')} = 1 AND {quote('id')} = {quote('slug')}"
        )

    _rewrite_analytics_sources(connection, existing_tables, form_slugs, question_types)


def rename_slug_indexes(apps, schema_editor, *, model_name, reverse=False):
    """Rename implicit indexes along with the old slug column.

    PostgreSQL keeps index names when a column is renamed. Both the regular
    history index and the varchar pattern index would otherwise collide with
    the indexes Django creates for the replacement slug field.
    """
    model = apps.get_model("caluma_form", model_name)
    table = model._meta.db_table
    with schema_editor.connection.cursor() as cursor:
        constraints = schema_editor.connection.introspection.get_constraints(
            cursor, table
        )

    old_column, new_column = ("id", "slug") if reverse else ("slug", "id")
    for suffix in ("", "_like"):
        old_name = schema_editor._create_index_name(table, [old_column], suffix=suffix)
        index = constraints.get(old_name)
        if index and index["index"] and index["columns"] == ["id"]:
            new_name = schema_editor._create_index_name(
                table, [new_column], suffix=suffix
            )
            schema_editor.rename_index(
                model,
                models.Index(fields=["id"], name=old_name),
                models.Index(fields=["id"], name=new_name),
            )


def check_snapshot_reversal(apps, schema_editor):
    """Allow rollback only while all definitions retain their legacy identities."""
    for model_name in ("Form", "Question", "Option", "FormQuestion", "QuestionOption"):
        for prefix in ("", "Historical"):
            model = apps.get_model("caluma_form", f"{prefix}{model_name}")
            if (
                model.objects.using(schema_editor.connection.alias)
                .exclude(snapshot_id=1, id=models.F("slug"))
                .exists()
            ):
                raise IrreversibleError(
                    "Cannot remove snapshots after snapshot-specific identities "
                    f"have been created in {model._meta.label}."
                )


def _slug_field(natural_key, **kwargs):
    if natural_key:
        return models.CharField(max_length=255, editable=False, **kwargs)
    return models.SlugField(max_length=127, **kwargs)


def _snapshot_identity_operations(model_name, natural_key, historical):
    operations = []
    if historical:
        model_name = f"historical{model_name}"

    if not natural_key:
        # Reuse the existing PK column so PostgreSQL preserves every incoming
        # FK, including workflow and third-party relations. Restore the separate
        # slug column below. The resulting ID is the original slug, unchanged.
        operations.append(migrations.RenameField(model_name, "slug", "id"))
        operations.append(
            migrations.RunPython(
                partial(rename_slug_indexes, model_name=model_name),
                partial(rename_slug_indexes, model_name=model_name, reverse=True),
            )
        )

    operations.extend(
        [
            migrations.AlterField(
                model_name,
                "id",
                models.CharField(
                    max_length=276,
                    editable=False,
                    **(
                        {"db_index": True}
                        if historical
                        else {"primary_key": True, "serialize": False}
                    ),
                ),
            ),
            migrations.AddField(
                model_name, "slug", _slug_field(natural_key, null=True)
            ),
            migrations.AddField(
                model_name,
                "snapshot",
                models.ForeignKey(
                    to="caluma_snapshot.snapshot",
                    on_delete=models.DO_NOTHING if historical else models.PROTECT,
                    related_name="+",
                    default=1,
                    **(
                        {"blank": True, "null": True, "db_constraint": False}
                        if historical
                        else {}
                    ),
                ),
                preserve_default=not natural_key,
            ),
        ]
    )
    return operations


def form_snapshot_operations():
    """Build the initial form-definition migration without changing existing IDs."""
    definitions = {
        "form": False,
        "question": False,
        "option": False,
        "formquestion": True,
        "questionoption": True,
    }
    operations = []
    for model_name, natural_key in definitions.items():
        for historical in (False, True):
            operations.extend(
                _snapshot_identity_operations(model_name, natural_key, historical)
            )

    operations.append(
        migrations.RunPython(populate_snapshot_slugs, migrations.RunPython.noop)
    )

    for model_name, natural_key in definitions.items():
        for prefix in ("", "historical"):
            operations.append(
                migrations.AlterField(
                    f"{prefix}{model_name}", "slug", _slug_field(natural_key)
                )
            )
        operations.append(
            migrations.AddConstraint(
                model_name,
                models.UniqueConstraint(
                    fields=["slug", "snapshot"],
                    name=f"caluma_form_{model_name}_slug_snapshot_uniq",
                ),
            )
        )
        operations.append(
            migrations.AddConstraint(
                model_name,
                models.CheckConstraint(
                    condition=models.Q(snapshot_id=1, id=models.F("slug"))
                    | models.Q(
                        id=Concat(
                            "slug",
                            models.Value(":"),
                            Cast("snapshot_id", output_field=models.CharField()),
                        )
                    ),
                    name=f"caluma_form_{model_name}_snapshot_id_valid",
                ),
            )
        )
    # This runs first on rollback, before any schema changes. Multiple snapshots
    # and newly suffixed IDs cannot be represented by the legacy schema.
    operations.append(
        migrations.RunPython(migrations.RunPython.noop, check_snapshot_reversal)
    )
    return operations
