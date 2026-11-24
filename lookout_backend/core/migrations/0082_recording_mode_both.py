from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0081_systemsettings_far_mode"),
    ]

    operations = [
        migrations.AlterField(
            model_name="systemsettings",
            name="recording_mode",
            field=models.CharField(
                choices=[("clip", "Processed"), ("source", "Normal"), ("both", "Both")],
                default="clip",
                max_length=10,
            ),
        ),
    ]
