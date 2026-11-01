"""Hard block on every database write made by the detection worker.

Installed as a Django `execute_wrapper` on every connection. A blocked statement is
logged to blocked_writes.log and raises, so nothing can reach LookOut's database
(which is additionally opened read-only by lookout_settings_wrapper).
"""
import re
import threading

WRITE_VERBS = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|ALTER|DROP)\b", re.I)


class BlockedWrite(RuntimeError):
    pass


class Guard:
    def __init__(self, log_path):
        self.log_path = str(log_path)
        self.blocked = []
        self._lock = threading.Lock()
        self.active = False

    def __call__(self, execute, sql, params, many, context):
        if WRITE_VERBS.match(sql or ""):
            line = f"BLOCKED: {sql[:300]!r} params={str(params)[:200]}"
            with self._lock:
                self.blocked.append(line)
                with open(self.log_path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            raise BlockedWrite(line)
        return execute(sql, params, many, context)


def install(log_path):
    """Attach the guard to the current and every future DB connection. Returns the Guard."""
    from django.db import connection, connections
    from django.db.backends.signals import connection_created

    guard = Guard(log_path)
    open(guard.log_path, "a", encoding="utf-8").close()      # the log always exists, even when empty

    def attach(conn):
        if guard not in conn.execute_wrappers:
            conn.execute_wrappers.append(guard)

    def on_created(sender, connection, **kw):
        attach(connection)

    connection_created.connect(on_created, weak=False, dispatch_uid="alpha_eval_dbguard")
    for conn in connections.all():
        attach(conn)
    attach(connection)
    guard.active = True
    return guard
