from django.db import migrations, models

OLD_DEFAULT = 60
NEW_DEFAULT = 30


def lower_untouched_radius(apps, schema_editor):
    """Bring an existing settings row down to the new default.

    AlterField alone only changes what a NEW row would be born with, and
    SystemSettings is a single long-lived row — so every earlier change to a
    disposal default (0062, 0063) left running installs on the old value and
    silently did nothing. This one is the reason the radius was still 60 after
    the default moved.

    Only where it is still EXACTLY the old default, which is the test for
    "nobody has touched this". An operator who deliberately set 80 for a wide
    view keeps their 80; changing a number somebody chose on purpose, during a
    migration they did not ask for, is worse than leaving it stale.
    """
    SystemSettings = apps.get_model("core", "SystemSettings")
    SystemSettings.objects.filter(
        disposal_cooldown_dist=OLD_DEFAULT
    ).update(disposal_cooldown_dist=NEW_DEFAULT)


def restore_untouched_radius(apps, schema_editor):
    SystemSettings = apps.get_model("core", "SystemSettings")
    SystemSettings.objects.filter(
        disposal_cooldown_dist=NEW_DEFAULT
    ).update(disposal_cooldown_dist=OLD_DEFAULT)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0068_alert_annotated_offset'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemsettings',
            name='disposal_cooldown_dist',
            field=models.PositiveSmallIntegerField(default=NEW_DEFAULT),
        ),
        migrations.RunPython(lower_untouched_radius, restore_untouched_radius),
    ]
