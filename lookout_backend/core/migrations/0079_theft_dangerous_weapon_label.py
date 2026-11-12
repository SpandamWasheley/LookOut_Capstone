"""Rename the theft violation to "Dangerous Weapon Observed".

What an alert is CALLED, not what it is. The code stays "theft", so every
alert, citation and statistic already filed keeps pointing at the same row and
nothing has to be re-matched -- this is the same display-only rename 0024 and
0026 did before it.

The name change is a real one, though. "Holdup" names a crime that has been
committed; the detector finds a person holding a knife near another person,
which is a thing an officer should be sent to look at and may well turn out to
be something else entirely. Naming the alert after what the camera can
actually establish is both more honest on a citation slip and fairer to
whoever is standing there.

The seeds and the three detectors that get_or_create this row are updated in
the same change, but their defaults only apply when the row is CREATED -- an
installation that already has one would keep the old name for ever without
this.
"""
from django.db import migrations

NEW = "Dangerous Weapon Observed"
OLD = "Holdup in Public Area"


def rename(apps, schema_editor):
    ViolationType = apps.get_model("core", "ViolationType")
    # Only the row that still carries the old name: an operator who has since
    # renamed it themselves has made a choice, and a migration should not
    # overrule it.
    ViolationType.objects.filter(code="theft", label=OLD).update(label=NEW)


def back(apps, schema_editor):
    ViolationType = apps.get_model("core", "ViolationType")
    ViolationType.objects.filter(code="theft", label=NEW).update(label=OLD)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0078_continuous_recording"),
    ]

    operations = [
        migrations.RunPython(rename, back),
    ]
