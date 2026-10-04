"""
LookOut — context collector.

Drop this in the project root (next to manage.py) and run:

    python collect_context.py

It writes lookout_context.md, which you upload back to the chat. It gathers
the model fields, the detection commands, the URL routing, a redacted copy of
settings.py and the installed packages.

Secrets are redacted before writing. Open the file and skim it before you
upload it anyway.
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "lookout_context.md"

# Redaction keeps the KEY and blanks only the VALUE, matching both KEY=VALUE
# and "KEY": VALUE. Matching the key rather than the whole line matters in both
# directions: `"PASSWORD": "x"` inside a dict literal has no `=` and used to
# slip through untouched, while `path("auth/login/", name="login")` merely
# CONTAINS "auth" and used to be destroyed for no reason.
SECRET_RE = re.compile(
    r"SECRET|PASSWORD|PASSWD|TOKEN|CREDENTIAL|DSN|APIKEY"
    r"|API[_-]?KEY|PRIVATE[_-]?KEY|ACCESS[_-]?KEY|AUTH[_-]?KEY"
    r"|[_-]KEY|\bKEY\b",
    re.IGNORECASE,
)
# Key names that contain a hint word but never hold a secret — without these,
# every `primary_key=True` in models.py comes out blanked.
SAFE_KEYS = {"primary_key", "foreign_key", "sort_key", "cache_key",
             "order_key", "group_key", "dict_key"}
# Matches only KEY followed by its separator — NOT the value.
#
# Capturing the value was a bug twice over. It stopped at the first comma, so
# `config('X', default='real-secret')` leaked its tail; and because the group
# was greedy, a match on an outer key swallowed any nested one, so
# `DATABASES = {"default": {"PASSWORD": "x"}}` hid the PASSWORD from finditer's
# non-overlapping scan entirely. Matching just the key keeps every match short,
# so every key on a line is seen, and redaction runs to end of line.
KV = re.compile(r"[\"']?(?P<key>[A-Za-z_][\w.-]*)[\"']?(?P<sep>\s*[:=]\s*)")
# Credentials embedded in a URL. Covers user:pass@host AND token-only userinfo
# (https://ghp_xxxx@github.com/...), which pip freeze emits for a package
# installed from a private git repo — a well-worn way to leak a token.
URL_CREDS = re.compile(r"(\w+://)[^/\s@]+@")

WATCH_COMMANDS = ["watch_drinking", "watch_smoking", "watch_thief", "watch_parking"]
WANTED_FUNCS = {"handle", "add_arguments", "_create_alert", "_run_stream",
                "_open_capture", "_record_clip"}

chunks = []


def add(title, body, lang=""):
    """Append a section. EVERY section is redacted here rather than at each
    call site: requirements.txt, pip freeze and the model dump were the three
    that nobody remembered to redact, and they are exactly the ones that can
    carry an index URL or a git+https token."""
    body = (body or "").strip()
    if not body:
        body = "(not found)"
    else:
        body = redact(body)
    fence = f"```{lang}\n{body}\n```" if lang else body
    chunks.append(f"\n## {title}\n\n{fence}\n")


def _is_secret(key):
    # Lowercase bare `key=` is Python's sort idiom (max(xs, key=lambda ...)),
    # not a credential; uppercase bare `KEY =` is the constant convention and
    # stays covered. Case is the only thing separating the two.
    if key == "key" or key.lower() in SAFE_KEYS:
        return False
    return bool(SECRET_RE.search(key))


def redact(text):
    """Blank the value of any secret-looking key, through to END OF LINE.

    Stopping the value at the first comma is not enough, and this project has
    the proof in its own settings:

        AWS_SECRET_ACCESS_KEY = config('AWS_SECRET_ACCESS_KEY', default='')

    Cutting at the comma leaves `, default='')` in the output — harmless while
    the default is empty, and a published credential the day somebody inlines a
    real one. There is no safe way to know which half of such a line holds the
    secret, so the whole remainder goes.

    The cost is a few syntactically broken lines in a file nobody executes.
    """
    out = []
    for line in text.splitlines():
        for match in KV.finditer(line):
            if not _is_secret(match.group("key")):
                continue
            line = line[:match.end("sep")] + "'<REDACTED>'"
            break                      # everything after it went with it
        out.append(URL_CREDS.sub(r"\1<REDACTED>@", line))
    return "\n".join(out)


def read(path, limit=None):
    p = ROOT / path if not Path(path).is_absolute() else Path(path)
    if not p.exists():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(could not read: {exc})"
    if limit and len(text) > limit:
        text = text[:limit] + f"\n\n... truncated at {limit} chars ..."
    return text


def find_one(*patterns):
    """Return the first matching path under the project, skipping junk dirs."""
    skip = {".git", "node_modules", "venv", ".venv", "env", "__pycache__",
            "staticfiles", "media", "dist", "build"}
    for pattern in patterns:
        for path in ROOT.rglob(pattern):
            if any(part in skip for part in path.parts):
                continue
            return path
    return None


def extract_funcs(source, wanted):
    """Pull named functions/methods out of a module using the AST."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"(could not parse: {exc})"
    lines = source.splitlines()
    found = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in wanted:
            start = min(d.lineno for d in node.decorator_list) - 1 if node.decorator_list \
                else node.lineno - 1
            end = node.end_lineno
            found.append("\n".join(lines[start:end]))
    return "\n\n# " + "-" * 60 + "\n\n".join(found) if found else None


# ---------------------------------------------------------------------------
# 1. Django model fields
# ---------------------------------------------------------------------------

SNIPPET = (
    "import django, json;"
    "from django.apps import apps;"
    "out={};"
    "names=['Alert','Camera','SystemSettings','DetectionJob','Officer','Guardian'];"
    "\nfor m in apps.get_models():\n"
    "    if m.__name__ in names:\n"
    "        out[m.__name__]=[(f.name, f.get_internal_type()) "
    "for f in m._meta.get_fields() if hasattr(f,'get_internal_type')]\n"
    "print(json.dumps(out, indent=2))"
)

try:
    result = subprocess.run(
        [sys.executable, "manage.py", "shell", "-c", SNIPPET],
        cwd=ROOT, capture_output=True, text=True, timeout=180,
    )
    model_dump = result.stdout.strip() or result.stderr.strip()
except Exception as exc:  # noqa: BLE001
    model_dump = f"(failed to run manage.py shell: {exc})"

add("Model fields", model_dump, "json")

# ---------------------------------------------------------------------------
# 2. models.py source
# ---------------------------------------------------------------------------

models_path = find_one("models.py")
if models_path:
    add(f"models.py  ({models_path.relative_to(ROOT)})",
        redact(models_path.read_text(encoding="utf-8", errors="replace")), "python")

# ---------------------------------------------------------------------------
# 3. Detection commands
# ---------------------------------------------------------------------------

for name in WATCH_COMMANDS:
    path = find_one(f"{name}.py")
    if not path:
        add(f"{name}.py", "(not found)")
        continue
    source = path.read_text(encoding="utf-8", errors="replace")
    if name == "watch_drinking":
        # full file for the one we are patching first
        add(f"{name}.py  (full, {path.relative_to(ROOT)})", redact(source), "python")
    else:
        funcs = extract_funcs(source, WANTED_FUNCS)
        add(f"{name}.py  (key functions, {path.relative_to(ROOT)})",
            redact(funcs or source[:8000]), "python")

# ---------------------------------------------------------------------------
# 4. Vision package layout
# ---------------------------------------------------------------------------

vision = find_one("vision")
if vision and vision.is_dir():
    listing = "\n".join(
        sorted(str(p.relative_to(ROOT)) for p in vision.rglob("*.py"))
    )
    add("core/vision/ contents", listing)

# ---------------------------------------------------------------------------
# 5. URLs
# ---------------------------------------------------------------------------

for path in sorted(ROOT.rglob("urls.py")):
    if any(p in {".git", "venv", ".venv", "node_modules", "__pycache__"}
           for p in path.parts):
        continue
    add(f"urls.py  ({path.relative_to(ROOT)})",
        redact(path.read_text(encoding="utf-8", errors="replace")), "python")

# ---------------------------------------------------------------------------
# 6. settings.py (redacted)
# ---------------------------------------------------------------------------

settings_path = find_one("settings.py")
if settings_path:
    add(f"settings.py  (REDACTED, {settings_path.relative_to(ROOT)})",
        redact(settings_path.read_text(encoding="utf-8", errors="replace")), "python")

# ---------------------------------------------------------------------------
# 7. Requirements
# ---------------------------------------------------------------------------

add("requirements.txt", read("requirements.txt", 6000), "text")

try:
    freeze = subprocess.run([sys.executable, "-m", "pip", "freeze"],
                            capture_output=True, text=True, timeout=120)
    add("pip freeze", freeze.stdout, "text")
except Exception as exc:  # noqa: BLE001
    add("pip freeze", f"(failed: {exc})")

# ---------------------------------------------------------------------------
# 8. Environment variable NAMES only
# ---------------------------------------------------------------------------

env_names = []
for candidate in (".env", ".env.local", ".env.production"):
    text = read(candidate)
    if not text:
        continue
    keys = [ln.split("=", 1)[0].strip() for ln in text.splitlines()
            if "=" in ln and not ln.strip().startswith("#")]
    env_names.append(f"{candidate}:\n  " + "\n  ".join(keys))

live = [k for k in os.environ if k.startswith("LOOKOUT") or k in
        ("DATABASE_URL", "DJANGO_SETTINGS_MODULE", "RENDER_EXTERNAL_URL")]
if live:
    env_names.append("currently set in this shell:\n  " + "\n  ".join(sorted(live)))

add("Environment variable names (values NOT included)",
    "\n\n".join(env_names) or "(no .env files found)")

# ---------------------------------------------------------------------------
# 9. Migrations state
# ---------------------------------------------------------------------------

try:
    mig = subprocess.run([sys.executable, "manage.py", "showmigrations", "core"],
                         cwd=ROOT, capture_output=True, text=True, timeout=180)
    add("showmigrations core", mig.stdout or mig.stderr, "text")
except Exception as exc:  # noqa: BLE001
    add("showmigrations core", f"(failed: {exc})")

# ---------------------------------------------------------------------------
# 10. Git state
# ---------------------------------------------------------------------------

git_info = []
for label, cmd in (
    ("branch", ["git", "rev-parse", "--abbrev-ref", "HEAD"]),
    ("commit", ["git", "log", "-1", "--oneline"]),
    ("status", ["git", "status", "--short"]),
):
    try:
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
        git_info.append(f"{label}: {r.stdout.strip() or '(none)'}")
    except Exception:  # noqa: BLE001
        git_info.append(f"{label}: (git unavailable)")
add("Git", "\n".join(git_info), "text")

# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

header = (
    "# LookOut — project context dump\n\n"
    f"Generated from: `{ROOT}`\n\n"
    "Secrets were auto-redacted. Skim before uploading.\n"
)
OUT.write_text(header + "".join(chunks), encoding="utf-8")

size_kb = OUT.stat().st_size / 1024
print(f"\nWrote {OUT}  ({size_kb:.0f} KB)")
print("Open it, skim for anything sensitive, then upload it to the chat.")
