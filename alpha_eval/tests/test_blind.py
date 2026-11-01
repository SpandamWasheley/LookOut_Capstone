"""Verification 3: the detection worker never receives (or can read) the answer key."""
import ast
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FORBIDDEN_MODULES = {"db", "matching", "scoring", "queue_runner", "ui_common", "exporter", "streamlit", "sqlite3"}
WORKER_FILES = ["worker.py", "capture.py", "videoclock.py", "dbguard.py", "lookout_settings_wrapper.py"]


def imports_of(path, only_function=None):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [n for n in ast.walk(tree) if (not only_function) or True]
    if only_function:
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == only_function)
        nodes = list(ast.walk(fn))
    found = set()
    for n in nodes:
        if isinstance(n, ast.Import):
            found |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            found.add(n.module.split(".")[0])
    return found


def test_detection_modules_do_not_import_the_scoring_side():
    for f in WORKER_FILES:
        bad = imports_of(HERE / f) & (FORBIDDEN_MODULES - {"sqlite3"})
        # worker.py's `daemon` branch lazily imports queue_runner; the detection path (run_detection/run_check) must not
        if f == "worker.py":
            for fn in ("run_detection", "run_check", "prepare_work_dir", "git_info", "settings_dump"):
                assert not (imports_of(HERE / f, fn) & FORBIDDEN_MODULES), (f, fn)
            bad = bad - {"queue_runner"}
        assert not bad, (f, bad)


def test_worker_source_never_mentions_the_answer_key():
    for f in WORKER_FILES:
        src = (HERE / f).read_text(encoding="utf-8").lower()
        for word in ("answer_key", "answer key", "action_start", "key_rows"):
            assert word not in src, (f, word)


def test_worker_run_inputs_are_exactly_these():
    src = (HERE / "worker.py").read_text(encoding="utf-8")
    run_args = re.findall(r'r\.add_argument\("(--[a-z-]+)"', src)
    assert run_args == ["--run-id", "--video", "--parking-threshold", "--drinking-duration", "--clock", "--run-dir", "--slow"]
