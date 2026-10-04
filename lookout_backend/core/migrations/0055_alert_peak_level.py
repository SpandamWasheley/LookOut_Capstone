"""Alert.peak_level: the highest status an event ever reached.

`level` moves both ways, so an event that reached Possible and then faded back
to Monitoring silently dropped out of Potential Violations — out from under a
reviewer who was already looking at it. Listing is decided by this field
instead; the badge still shows `level`, the live truth.

Existing rows are backfilled to their current level: that is the only peak
history there is for them, and it keeps every alert exactly where it is today
rather than emptying the list on deploy.
"""
from django.db import migrations, models


def seed_peak_from_level(apps, schema_editor):
    Alert = apps.get_model("core", "Alert")
    Alert.objects.exclude(level="").update(peak_level=models.F("level"))


def unseed(apps, schema_editor):
    """No-op: reversing drops the column anyway."""


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0054_alert_status_label_dismissed"),
    ]

    operations = [
        migrations.AddField(
            model_name="alert",
            name="peak_level",
            field=models.CharField(
                blank=True,
                choices=[
                    ("none", "None"),
                    ("monitoring", "Monitoring"),
                    ("warning", "Possible"),
                    ("violation", "Likely"),
                ],
                max_length=10,
            ),
        ),
        migrations.RunPython(seed_peak_from_level, unseed),
    ]
