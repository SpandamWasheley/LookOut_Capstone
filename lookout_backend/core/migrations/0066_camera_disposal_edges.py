from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0065_disposal_collection_window"),
    ]

    operations = [
        migrations.AddField(
            model_name="camera",
            name="disposal_edges",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="camera",
            name="disposal_edges_height",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="camera",
            name="disposal_edges_width",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
