"""
db/audit.py — Audit log write and query helpers.
"""

import time

from core.logger import log_audit
from db.core import _db_enqueue
from db.helpers import db_query, db_cursor


def db_log_audit(actor: str, ip: str, action: str, target: str = '', detail: str = ''):
    """Append one audit entry; trim to last 2000 rows."""
    _t = f" -> {target}" if target else ""
    _d = f" | {detail}" if detail else ""
    log_audit.info(f"{actor} [{ip}] {action}{_t}{_d}")

    def _write():
        from db.backend import is_pg
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"INSERT INTO audit_log(ts,actor,ip,action,target,detail) "
                f"VALUES({ph},{ph},{ph},{ph},{ph},{ph})",
                (time.time(), actor, ip, action, target, detail)
            )
            cur.execute("DELETE FROM audit_log WHERE id NOT IN "
                        "(SELECT id FROM audit_log ORDER BY ts DESC LIMIT 2000)")
    _db_enqueue(_write)


def db_get_audit(limit: int = 200, action_prefixes: list | None = None) -> list:
    """Return newest-first audit entries.

    `action_prefixes`: optional list of strings. When provided, only rows
    whose `action` starts with one of the prefixes are returned. Used by
    feature-specific activity panes (Auto-Discovery, etc.) without moving
    the audit log into a feature silo.
    """
    if action_prefixes:
        # Build a parameterized `action LIKE ? OR action LIKE ? ...` clause.
        # Prefix-anchored LIKE is index-friendly on both SQLite + PG.
        like_clauses = " OR ".join(["action LIKE ?"] * len(action_prefixes))
        params = tuple(p + "%" for p in action_prefixes) + (limit,)
        rows = db_query("main",
                        f"SELECT ts,actor,ip,action,target,detail FROM audit_log "
                        f"WHERE {like_clauses} "
                        f"ORDER BY ts DESC LIMIT ?",
                        params)
    else:
        rows = db_query("main",
                        "SELECT ts,actor,ip,action,target,detail FROM audit_log "
                        "ORDER BY ts DESC LIMIT ?",
                        (limit,))
    return [{"ts":     r["ts"],
             "actor":  r["actor"],
             "ip":     r["ip"],
             "action": r["action"],
             "target": r["target"],
             "detail": r["detail"]} for r in rows]
