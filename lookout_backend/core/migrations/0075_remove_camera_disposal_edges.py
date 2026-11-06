from django.db import migrations


def disposal_edges_into_edges(apps, schema_editor):
    """Carry any area drawn under 0066's dedicated columns into Camera.edges.

    The disposal area now lives in Camera.edges as a "disposal" entry (see
    core/zones.py), sharing edges_width / edges_height with the road. An area
    saved in the old columns would otherwise vanish with them, so it is moved
    first: re-keyed, retyped, and rescaled to the frame size the road uses (or,
    if the camera has no road, it sets that size). A camera that already has a
    "disposal" entry keeps it -- the newer storage wins.
    """
    Camera = apps.get_model("core", "Camera")
    for cam in Camera.objects.all():
        old = cam.disposal_edges if isinstance(cam.disposal_edges, dict) else {}
        spec = next((s for s in old.values()
                     if isinstance(s, dict) and len(s.get("points") or []) >= 3), None)
        edges = cam.edges if isinstance(cam.edges, dict) else {}
        if spec is None or any(isinstance(s, dict) and s.get("type") == "disposal"
                               for s in edges.values()):
            continue
        ow, oh = cam.disposal_edges_width, cam.disposal_edges_height
        if cam.edges_width and cam.edges_height and ow and oh:
            sx, sy = cam.edges_width / ow, cam.edges_height / oh
        else:
            sx = sy = 1.0
            if not (cam.edges_width and cam.edges_height):
                cam.edges_width, cam.edges_height = ow, oh
        points = [[round(x * sx), round(y * sy)] for x, y in spec["points"]]
        cam.edges = {**edges, "disposal": {"type": "disposal", "points": points}}
        cam.save(update_fields=["edges", "edges_width", "edges_height"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0074_merge_20261106_2145"),
    ]

    operations = [
        migrations.RunPython(disposal_edges_into_edges, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="camera",
            name="disposal_edges",
        ),
        migrations.RemoveField(
            model_name="camera",
            name="disposal_edges_height",
        ),
        migrations.RemoveField(
            model_name="camera",
            name="disposal_edges_width",
        ),
    ]
