Drop one clear, front-facing reference photo per person in this folder.

The filename (without extension) becomes the person's name, e.g.:
    Juan_DelaCruz.jpg
    Maria_Santos.png

On first run, detect_image.py enrolls each photo and caches the embeddings to
_embeddings.json. Add --rebuild-faces after you add/remove photos to refresh
the cache.

Accepted formats: .jpg .jpeg .png
