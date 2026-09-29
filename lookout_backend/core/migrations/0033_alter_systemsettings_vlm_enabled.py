"""Turn VLM verification on by default, including on existing rows.

A changed field default only applies to rows created AFTER the migration, so
the AlterField alone would leave every existing install with the old False and
the feature would appear not to work. The data migration below flips them.

This is safe to do unconditionally, which it normally would not be: the field
was introduced one migration ago (0032) and no UI has ever exposed it, so a
stored False is the old default rather than somebody's decision. Any later
change to this field WOULD be a decision and must not be overwritten like this.

Turning it on does not make the system depend on it. With no credentials
configured, vlm.build_verifier returns an inert verifier and every detector
behaves exactly as it did before -- so "on" here means "use it when it is
usable", and the no-key path is still the silent, fully-working default.
"""

from django.db import migrations, models


def enable_on_existing_rows(apps, schema_editor):
    SystemSettings = apps.get_model("core", "SystemSettings")
    SystemSettings.objects.filter(vlm_enabled=False).update(vlm_enabled=True)


def disable_on_existing_rows(apps, schema_editor):
    """Reverse: restore the pre-0033 state so the migration is undoable."""
    SystemSettings = apps.get_model("core", "SystemSettings")
    SystemSettings.objects.filter(vlm_enabled=True).update(vlm_enabled=False)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_alert_cues_alert_level_alert_reviewed_valid_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemsettings',
            name='vlm_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(enable_on_existing_rows, disable_on_existing_rows),
    ]
