"""
db/audit.py — Audit log write and query helpers.
"""
from __future__ import annotations

import re
import time

import core.settings as _settings
from core.logger import log_audit
from db.core import _db_enqueue
from db.helpers import db_query, db_cursor

_CTRL_RE = re.compile(r'[\r\n\t\x00-\x1f\x7f]')


def _sanitize(s, max_len: int = 512) -> str:
    """Strip control characters (esp. CR/LF) and cap length.

    `actor`/`target`/`detail` are user-controlled (e.g. the login username on
    a failed-login audit). Embedded newlines would forge extra lines in the
    audit/app log files, which are later parsed back as genuine entries (log
    injection). The DB columns are parameterized and safe regardless; this
    protects the file-based log."""
    return _CTRL_RE.sub(' ', str(s or ''))[:max_len]


def db_log_audit(actor: str, ip: str, action: str, target: str = '', detail: str = ''):
    """Append one audit entry; trim to the configured `audit_trim_cap` rows."""
    actor  = _sanitize(actor, 256)
    ip     = _sanitize(ip, 64)
    action = _sanitize(action, 128)
    target = _sanitize(target, 512)
    detail = _sanitize(detail, 1024)
    _t = f" -> {target}" if target else ""
    _d = f" | {detail}" if detail else ""
    log_audit.info(f"{actor} [{ip}] {action}{_t}{_d}")

    def _write():
        from db.backend import is_pg
        try:
            cap = max(1000, min(1_000_000, int(_settings.get('audit_trim_cap', 50000) or 50000)))
        except (TypeError, ValueError):
            cap = 50000
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"INSERT INTO audit_log(ts,actor,ip,action,target,detail) "
                f"VALUES({ph},{ph},{ph},{ph},{ph},{ph})",
                (time.time(), actor, ip, action, target, detail)
            )
            cur.execute(
                f"DELETE FROM audit_log WHERE id NOT IN "
                f"(SELECT id FROM audit_log ORDER BY ts DESC LIMIT {ph})",
                (cap,)
            )
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


def db_query_audit(since: float = None, until: float = None,
                   actor: str = None, action_prefix: str = None,
                   limit: int = 100, offset: int = 0) -> list:
    """Filtered, paginated audit query (backs the MCP get_audit_log tool).

    Newest-first. All filters optional: time window [since, until), an exact
    `actor`, and a prefix-anchored `action` (index-friendly LIKE on both
    backends). Returns list of dict rows."""
    where, params = [], []
    if since is not None:
        where.append("ts >= ?");          params.append(float(since))
    if until is not None:
        where.append("ts < ?");           params.append(float(until))
    if actor:
        where.append("actor = ?");        params.append(actor)
    if action_prefix:
        where.append("action LIKE ?");    params.append(action_prefix + "%")
    W = (" WHERE " + " AND ".join(where)) if where else ""
    rows = db_query("main",
        f"SELECT ts, actor, ip, action, target, detail FROM audit_log{W} "
        f"ORDER BY ts DESC LIMIT ? OFFSET ?",
        tuple(params) + (int(limit), int(offset)))
    return [{"ts": r["ts"], "actor": r["actor"], "ip": r["ip"],
             "action": r["action"], "target": r["target"],
             "detail": r["detail"]} for r in rows]
