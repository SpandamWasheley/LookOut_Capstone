"""Tiny read-only video server (HTTP Range support, correct video MIME types) for the in-browser preview.

Streamlit's own static folder serves .mp4 as text/plain and caps files at 200 MB, so the preview player uses this
server instead. It only serves files that live directly in alpha_eval/clips/ (no sub-folders, no traversal).
"""
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

MIME = {".mp4": "video/mp4", ".mov": "video/quicktime", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
        ".jpg": "image/jpeg", ".png": "image/png"}
_state = {"port": None}


def make_handler(root: Path):
    root = root.resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _target(self):
            name = unquote(self.path.split("?", 1)[0]).lstrip("/")
            if not name or "/" in name or "\\" in name or name.startswith("."):
                return None
            p = (root / name).resolve()
            return p if p.parent == root and p.is_file() else None

        def _send(self, head_only):
            p = self._target()
            if p is None:
                self.send_error(404)
                return
            size = p.stat().st_size
            start, end, status = 0, size - 1, 200
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                if start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                end = min(end, size - 1)
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", MIME.get(p.suffix.lower()) or mimetypes.guess_type(p.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Access-Control-Allow-Origin", "*")
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head_only:
                return
            try:
                with open(p, "rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(1 << 20, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (ConnectionError, OSError):
                pass                                   # the browser closed the connection (seek): normal

        def do_GET(self):
            self._send(False)

        def do_HEAD(self):
            self._send(True)

    return Handler


def start(root, ports=range(8765, 8785)):
    """Start (once per process) and return the port."""
    if _state["port"]:
        return _state["port"]
    for port in ports:
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(Path(root)))
        except OSError:
            continue
        threading.Thread(target=srv.serve_forever, daemon=True, name="alpha-media").start()
        _state["port"] = port
        return port
    raise RuntimeError("no free port for the preview video server")
