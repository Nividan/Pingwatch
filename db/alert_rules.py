"""
db/alert_rules.py — CRUD helpers for the alert rules engine.

Tables: alert_rules, alert_rule_conditions, alert_rule_actions
All writes go through the main DB write-queue (_db_enqueue) when called from
routes; these helpers are also called directly from the engine (read-only).
"""

import json
import sqlite3
import time

from core.config import DB_PATH
from core.logger import log
from db.backend  import is_pg


# ── Internal helpers ──────────────────────────────────────────────

def _con():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _rule_row_to_dict(row) -> dict:
    if isinstance(row, dict):
        r = row
    else:
        r = dict(row) if row else {}
    # trigger_count / recover_count may not exist in older DBs
    try:
        tc = r["trigger_count"]
    except (KeyError, IndexError):
        tc = 1
    try:
        rc = r["recover_count"]
    except (KeyError, IndexError):
        rc = 1
    return {
        "id":              r["id"],
        "name":            r["name"],
        "enabled":         bool(r["enabled"]),
        "severity":        r["severity"],
        "condition_logic": r["condition_logic"],
        "cooldown_s":      r["cooldown_s"],
        "trigger_count":   int(tc or 1),
        "recover_count":   int(rc or 1),
        "sort_order":      r["sort_order"],
        "created_at":      r["created_at"],
        "updated_at":      r["updated_at"],
        "conditions":      [],
        "actions":         [],
    }


def _load_conditions_pg(cur, rule_id: int) -> list:
    cur.execute(
        "SELECT * FROM alert_rule_conditions WHERE rule_id=%s ORDER BY sort_order, id",
        (rule_id,)
    )
    return [{"id": r["id"], "field": r["field"], "op": r["op"],
             "value": r["value"], "sort_order": r["sort_order"]} for r in cur.fetchall()]


def _load_actions_pg(cur, rule_id: int) -> list:
    cur.execute(
        "SELECT * FROM alert_rule_actions WHERE rule_id=%s ORDER BY sort_order, id",
        (rule_id,)
    )
    result = []
    for r in cur.fetchall():
        cfg = {}
        try:
            cfg = json.loads(r["config"])
        except Exception:
            pass
        result.append({"id": r["id"], "atype": r["atype"],
                        "config": cfg, "sort_order": r["sort_order"]})
    return result


def _load_conditions(con, rule_id: int) -> list:
    rows = con.execute(
        "SELECT * FROM alert_rule_conditions WHERE rule_id=? ORDER BY sort_order, id",
        (rule_id,)
    ).fetchall()
    return [{"id": r["id"], "field": r["field"], "op": r["op"],
             "value": r["value"], "sort_order": r["sort_order"]} for r in rows]


def _load_actions(con, rule_id: int) -> list:
    rows = con.execute(
        "SELECT * FROM alert_rule_actions WHERE rule_id=? ORDER BY sort_order, id",
        (rule_id,)
    ).fetchall()
    result = []
    for r in rows:
        cfg = {}
        try:
            cfg = json.loads(r["config"])
        except Exception:
            pass
        result.append({"id": r["id"], "atype": r["atype"],
                        "config": cfg, "sort_order": r["sort_order"]})
    return result


# ── Public read functions ─────────────────────────────────────────

def db_list_rules() -> list:
    """Return all alert rules with their conditions and actions."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("SELECT * FROM alert_rules ORDER BY sort_order, id")
                rules = [_rule_row_to_dict(r) for r in cur.fetchall()]
                for rule in rules:
                    rule["conditions"] = _load_conditions_pg(cur, rule["id"])
                    rule["actions"]    = _load_actions_pg(cur, rule["id"])
            return rules
        except Exception as e:
            log.error(f"db_list_rules error: {e}")
            return []
    # SQLite
    con = _con()
    try:
        rules = [_rule_row_to_dict(r) for r in con.execute(
            "SELECT * FROM alert_rules ORDER BY sort_order, id"
        ).fetchall()]
        for rule in rules:
            rule["conditions"] = _load_conditions(con, rule["id"])
            rule["actions"]    = _load_actions(con, rule["id"])
        return rules
    except Exception as e:
        log.error(f"db_list_rules error: {e}")
        return []
    finally:
        con.close()


def db_get_rule(rule_id: int) -> dict | None:
    """Return a single rule with conditions and actions, or None."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("SELECT * FROM alert_rules WHERE id=%s", (rule_id,))
                row = cur.fetchone()
                if not row:
                    return None
                rule = _rule_row_to_dict(row)
                rule["conditions"] = _load_conditions_pg(cur, rule_id)
                rule["actions"]    = _load_actions_pg(cur, rule_id)
            return rule
        except Exception as e:
            log.error(f"db_get_rule error: {e}")
            return None
    # SQLite
    con = _con()
    try:
        row = con.execute("SELECT * FROM alert_rules WHERE id=?", (rule_id,)).fetchone()
        if not row:
            return None
        rule = _rule_row_to_dict(row)
        rule["conditions"] = _load_conditions(con, rule_id)
        rule["actions"]    = _load_actions(con, rule_id)
        return rule
    except Exception as e:
        log.error(f"db_get_rule error: {e}")
        return None
    finally:
        con.close()


# ── Public write functions (called inside _db_enqueue lambdas) ────

def db_create_rule(data: dict) -> int:
    """Insert a new rule (with conditions + actions). Returns new rule id."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "INSERT INTO alert_rules (name, enabled, severity, condition_logic, cooldown_s, trigger_count, recover_count, sort_order, created_at, updated_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (
                        data["name"],
                        1 if data.get("enabled", True) else 0,
                        data.get("severity", "warning"),
                        data.get("condition_logic", "AND"),
                        int(data.get("cooldown_s", 300)),
                        int(data.get("trigger_count", 1)),
                        int(data.get("recover_count", 1)),
                        int(data.get("sort_order", 0)),
                        now, now,
                    )
                )
                rule_id = cur.fetchone()["id"]
                _write_conditions_pg(cur, rule_id, data.get("conditions", []))
                _write_actions_pg(cur, rule_id, data.get("actions", []))
            return rule_id
        except Exception as e:
            log.error(f"db_create_rule error: {e}")
            return -1
    # SQLite
    con = _con()
    try:
        cur = con.execute(
            "INSERT INTO alert_rules (name, enabled, severity, condition_logic, cooldown_s, trigger_count, recover_count, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                data["name"],
                1 if data.get("enabled", True) else 0,
                data.get("severity", "warning"),
                data.get("condition_logic", "AND"),
                int(data.get("cooldown_s", 300)),
                int(data.get("trigger_count", 1)),
                int(data.get("recover_count", 1)),
                int(data.get("sort_order", 0)),
                now, now,
            )
        )
        rule_id = cur.lastrowid
        _write_conditions(con, rule_id, data.get("conditions", []))
        _write_actions(con, rule_id, data.get("actions", []))
        con.commit()
        return rule_id
    except Exception as e:
        log.error(f"db_create_rule error: {e}")
        return -1
    finally:
        con.close()


def db_update_rule(rule_id: int, data: dict) -> bool:
    """Replace a rule's fields, conditions, and actions atomically."""
    now = time.time()
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute(
                    "UPDATE alert_rules SET name=%s, enabled=%s, severity=%s, condition_logic=%s, "
                    "cooldown_s=%s, trigger_count=%s, recover_count=%s, sort_order=%s, updated_at=%s WHERE id=%s",
                    (
                        data["name"],
                        1 if data.get("enabled", True) else 0,
                        data.get("severity", "warning"),
                        data.get("condition_logic", "AND"),
                        int(data.get("cooldown_s", 300)),
                        int(data.get("trigger_count", 1)),
                        int(data.get("recover_count", 1)),
                        int(data.get("sort_order", 0)),
                        now, rule_id,
                    )
                )
                cur.execute("DELETE FROM alert_rule_conditions WHERE rule_id=%s", (rule_id,))
                cur.execute("DELETE FROM alert_rule_actions WHERE rule_id=%s", (rule_id,))
                _write_conditions_pg(cur, rule_id, data.get("conditions", []))
                _write_actions_pg(cur, rule_id, data.get("actions", []))
            return True
        except Exception as e:
            log.error(f"db_update_rule error: {e}")
            return False
    # SQLite
    con = _con()
    try:
        con.execute(
            "UPDATE alert_rules SET name=?, enabled=?, severity=?, condition_logic=?, "
            "cooldown_s=?, trigger_count=?, recover_count=?, sort_order=?, updated_at=? WHERE id=?",
            (
                data["name"],
                1 if data.get("enabled", True) else 0,
                data.get("severity", "warning"),
                data.get("condition_logic", "AND"),
                int(data.get("cooldown_s", 300)),
                int(data.get("trigger_count", 1)),
                int(data.get("recover_count", 1)),
                int(data.get("sort_order", 0)),
                now, rule_id,
            )
        )
        con.execute("DELETE FROM alert_rule_conditions WHERE rule_id=?", (rule_id,))
        con.execute("DELETE FROM alert_rule_actions WHERE rule_id=?", (rule_id,))
        _write_conditions(con, rule_id, data.get("conditions", []))
        _write_actions(con, rule_id, data.get("actions", []))
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_update_rule error: {e}")
        return False
    finally:
        con.close()


def db_delete_rule(rule_id: int) -> bool:
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("DELETE FROM alert_rule_conditions WHERE rule_id=%s", (rule_id,))
                cur.execute("DELETE FROM alert_rule_actions WHERE rule_id=%s", (rule_id,))
                cur.execute("DELETE FROM alert_rules WHERE id=%s", (rule_id,))
            return True
        except Exception as e:
            log.error(f"db_delete_rule error: {e}")
            return False
    # SQLite
    con = _con()
    try:
        con.execute("DELETE FROM alert_rule_conditions WHERE rule_id=?", (rule_id,))
        con.execute("DELETE FROM alert_rule_actions WHERE rule_id=?", (rule_id,))
        con.execute("DELETE FROM alert_rules WHERE id=?", (rule_id,))
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_delete_rule error: {e}")
        return False
    finally:
        con.close()


def db_set_rule_enabled(rule_id: int, enabled: bool) -> bool:
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                cur.execute("UPDATE alert_rules SET enabled=%s, updated_at=%s WHERE id=%s",
                            (1 if enabled else 0, time.time(), rule_id))
            return True
        except Exception as e:
            log.error(f"db_set_rule_enabled error: {e}")
            return False
    # SQLite
    con = _con()
    try:
        con.execute("UPDATE alert_rules SET enabled=?, updated_at=? WHERE id=?",
                    (1 if enabled else 0, time.time(), rule_id))
        con.commit()
        return True
    except Exception as e:
        log.error(f"db_set_rule_enabled error: {e}")
        return False
    finally:
        con.close()


def db_reorder_rules(id_list: list) -> None:
    """Set sort_order for each rule id in id_list (position = index)."""
    if is_pg():
        from db.pg_pool import pg_cursor
        try:
            with pg_cursor("main") as cur:
                for i, rule_id in enumerate(id_list):
                    cur.execute("UPDATE alert_rules SET sort_order=%s WHERE id=%s", (i, rule_id))
        except Exception as e:
            log.error(f"db_reorder_rules error: {e}")
        return
    # SQLite
    con = _con()
    try:
        for i, rule_id in enumerate(id_list):
            con.execute("UPDATE alert_rules SET sort_order=? WHERE id=?", (i, rule_id))
        con.commit()
    except Exception as e:
        log.error(f"db_reorder_rules error: {e}")
    finally:
        con.close()


# ── Private write helpers ─────────────────────────────────────────

def _write_conditions_pg(cur, rule_id: int, conditions: list):
    for i, c in enumerate(conditions):
        cur.execute(
            "INSERT INTO alert_rule_conditions (rule_id, field, op, value, sort_order) VALUES (%s,%s,%s,%s,%s)",
            (rule_id, c["field"], c["op"], str(c.get("value", "")), i)
        )


def _write_actions_pg(cur, rule_id: int, actions: list):
    for i, a in enumerate(actions):
        cfg = a.get("config", {})
        if not isinstance(cfg, str):
            cfg = json.dumps(cfg)
        cur.execute(
            "INSERT INTO alert_rule_actions (rule_id, atype, config, sort_order) VALUES (%s,%s,%s,%s)",
            (rule_id, a["atype"], cfg, i)
        )


def _write_conditions(con, rule_id: int, conditions: list):
    for i, c in enumerate(conditions):
        con.execute(
            "INSERT INTO alert_rule_conditions (rule_id, field, op, value, sort_order) VALUES (?,?,?,?,?)",
            (rule_id, c["field"], c["op"], str(c.get("value", "")), i)
        )


def _write_actions(con, rule_id: int, actions: list):
    for i, a in enumerate(actions):
        cfg = a.get("config", {})
        if not isinstance(cfg, str):
            cfg = json.dumps(cfg)
        con.execute(
            "INSERT INTO alert_rule_actions (rule_id, atype, config, sort_order) VALUES (?,?,?,?)",
            (rule_id, a["atype"], cfg, i)
        )
