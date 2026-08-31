from django.db import migrations


def rename_and_merge(apps, schema_editor):
    ViolationType = apps.get_model("core", "ViolationType")
    Alert = apps.get_model("core", "Alert")
    Citation = apps.get_model("core", "Citation")

    parking = ViolationType.objects.filter(label="Illegal Parking / Obstruction").first()
    if parking:
        parking.label = "Parking Obstruction"
        parking.save(update_fields=["label"])

    theft = ViolationType.objects.filter(label="Theft Violation").first()
    if theft:
        theft.label = "Theft (Holdup)"
        theft.save(update_fields=["label"])

    duplicate = ViolationType.objects.filter(label="Theft / Robbery").first()
    if duplicate and theft:
        Alert.objects.filter(type=duplicate).update(type=theft)
        for citation in Citation.objects.filter(violations=duplicate):
            citation.violations.remove(duplicate)
            citation.violations.add(theft)
        duplicate.delete()


def noop_reverse(apps, schema_editor):
    # Renames/merge aren't meaningfully reversible (the duplicate row and its
    # original FK targets are gone) — a reverse migration would have to guess.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_remove_citation_violator_name_and_more"),
    ]

    operations = [
        migrations.RunPython(rename_and_merge, noop_reverse),
    ]
