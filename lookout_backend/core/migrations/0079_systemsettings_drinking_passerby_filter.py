from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0078_drinking_group_duration_one_minute"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemsettings",
            name="drinking_passerby_filter",
            field=models.BooleanField(default=True),
        ),
    ]
