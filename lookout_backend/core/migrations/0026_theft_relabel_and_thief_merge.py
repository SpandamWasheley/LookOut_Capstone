from django.db import migrations


def relabel_and_merge(apps, schema_editor):
    ViolationType = apps.get_model("core", "ViolationType")
    Alert = apps.get_model("core", "Alert")
    Citation = apps.get_model("core", "Citation")

    # Final canonical labels — see 0024 for the same rename pattern. This
    # covers the live DB rows now; seed_demo.py is updated separately so a
    # future reseed doesn't revert it.
    theft = ViolationType.objects.filter(code="theft").first()
    if theft:
        theft.label = "Holdup in Public Area"
        theft.save(update_fields=["label"])

    parking = ViolationType.objects.filter(code="parking").first()
    if parking:
        parking.label = "Parking Obstruction in Area"
        parking.save(update_fields=["label"])

    # watch_thief.py used to create a SECOND row (code="thief") alongside the
    # seeded "theft" row, splitting every theft alert/citation across two
    # ViolationTypes — this is what actually broke citation counts and
    # "most common violation" stats, not just the display layer. Merging here
    # (rather than only fixing watch_thief.py going forward) re-points
    # whatever already landed on "thief" before the fix.
    thief = ViolationType.objects.filter(code="thief").first()
    if thief and theft:
        Alert.objects.filter(type=thief).update(type=theft)
        for citation in Citation.objects.filter(violations=thief):
            citation.violations.remove(thief)
            citation.violations.add(theft)
        thief.delete()


def noop_reverse(apps, schema_editor):
    # Same as 0024: not meaningfully reversible once the duplicate row and
    # its original FK targets are gone.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0025_citation_client_uuid"),
    ]

    operations = [
        migrations.RunPython(relabel_and_merge, noop_reverse),
    ]
