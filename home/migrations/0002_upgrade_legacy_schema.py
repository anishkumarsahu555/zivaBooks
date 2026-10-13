"""Bridge databases created before migration history was replaced by 0001.

The legacy database has applied 0002..0017, but the replacement initial migration
already contains their successor state. Only additive schema changes are made:
existing rows, columns, and tables are never removed. Fresh databases are a no-op.
"""
from django.db import migrations


def upgrade_legacy_schema(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM django_migrations WHERE app = 'home'")
        applied = {row[0] for row in cursor.fetchall()}
        if '0002_invoice_series' not in applied:
            return
        if '0017_inventoryunit_inventoryitem_unit_and_more' not in applied:
            raise RuntimeError(
                'Unsupported legacy schema: restore the original migration chain '
                'before upgrading databases older than home migration 0017.'
            )
        tables = set(connection.introspection.table_names(cursor))

    models = [m for m in apps.get_app_config('home').get_models()
              if m._meta.managed and not m._meta.proxy]
    existing = [m for m in models if m._meta.db_table in tables]
    # Foreign key SQL is deferred by Django until all new tables exist.
    for model in models:
        if model._meta.db_table not in tables:
            schema_editor.create_model(model)

    for model in existing:
        with connection.cursor() as cursor:
            columns = {c.name for c in connection.introspection.get_table_description(
                cursor, model._meta.db_table)}
        for field in model._meta.local_fields:
            if field.column not in columns:
                if not field.null and not field.has_default() and not field.empty_strings_allowed:
                    raise RuntimeError(f'Cannot safely populate {model._meta.label}.{field.name}')
                schema_editor.add_field(model, field)
        for field in model._meta.local_many_to_many:
            through = field.remote_field.through
            if through._meta.auto_created and through._meta.db_table not in tables:
                schema_editor.create_model(through)
                tables.add(through._meta.db_table)

        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
        for index in model._meta.indexes:
            if index.name not in constraints:
                schema_editor.add_index(model, index)
        for constraint in model._meta.constraints:
            if constraint.name not in constraints:
                schema_editor.add_constraint(model, constraint)


class Migration(migrations.Migration):
    atomic = True
    dependencies = [('home', '0001_initial')]
    # Deliberately irreversible: reversing must not delete newly populated data.
    operations = [migrations.RunPython(upgrade_legacy_schema)]
