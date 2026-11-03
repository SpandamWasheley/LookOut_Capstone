from django.db import migrations, models

OLD_DEFAULT = 2
NEW_DEFAULT = 12


def raise_untouched_still_time(apps, schema_editor):
    """Bring an existing settings row up to the new default.

    AlterField alone only changes what a NEW row would be born with, and
    SystemSettings is a single long-lived row — so on every running install
    the still requirement would stay at 2 seconds and this change would do
    nothing at all. Same reasoning, and same shape, as 0069.

    Only where it is still EXACTLY the old default, which is the test for
    "nobody has touched this". An operator who deliberately set 5 for a quiet
    side street keeps their 5; overwriting a number somebody chose on purpose,
    during a migration they did not ask for, is worse than leaving it stale.
    """
    SystemSettings = apps.get_model("core", "SystemSettings")
    SystemSettings.objects.filter(
        disposal_static_seconds=OLD_DEFAULT
    ).update(disposal_static_seconds=NEW_DEFAULT)


def lower_untouched_still_time(apps, schema_editor):
    SystemSettings = apps.get_model("core", "SystemSettings")
    SystemSettings.objects.filter(
        disposal_static_seconds=NEW_DEFAULT
    ).update(disposal_static_seconds=OLD_DEFAULT)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0069_alter_systemsettings_disposal_cooldown_dist'),
    ]

    operations = [
        migrations.AlterField(
            model_name='systemsettings',
            name='disposal_static_seconds',
            field=models.PositiveSmallIntegerField(default=NEW_DEFAULT),
        ),
        migrations.RunPython(raise_untouched_still_time, lower_untouched_still_time),
    ]
