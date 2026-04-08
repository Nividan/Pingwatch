"""
db/alert_rules.py — CRUD helpers for the alert rules engine.

Tables: alert_rules, alert_rule_conditions, alert_rule_actions
All writes go through the main DB write-queue (_db_enqueue) when called from
routes; these helpers are also called directly from the engine (read-only).
"""

import json
import time

from core.logger import log
from db.backend  import is_pg
from db.helpers  import db_query, db_query_one, db_execute, db_cursor


# ── Internal helpers ──────────────────────────────────────────────

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


def _parse_action_row(r):
    cfg = {}
    try:
        cfg = json.loads(r["config"])
    except Exception:
        pass
    return {"id": r["id"], "atype": r["atype"], "config": cfg,
            "sort_order": r["sort_order"]}


def _parse_condition_row(r):
    return {"id": r["id"], "field": r["field"], "op": r["op"],
            "value": r["value"], "sort_order": r["sort_order"]}


# ── Public read functions ─────────────────────────────────────────

def db_list_rules() -> list:
    """Return all alert rules with their conditions and actions (batch-loaded)."""
    rules = [_rule_row_to_dict(r) for r in
             db_query("main", "SELECT * FROM alert_rules ORDER BY sort_order, id")]
    if not rules:
        return rules
    rule_ids = [r["id"] for r in rules]

    # Batch-load conditions + actions in two queries (vs one per rule)
    try:
        with db_cursor("main") as cur:
            if is_pg():
                cur.execute(
                    "SELECT * FROM alert_rule_conditions WHERE rule_id = ANY(%s) "
                    "ORDER BY rule_id, sort_order, id",
                    (rule_ids,)
                )
            else:
                placeholders = ",".join("?" * len(rule_ids))
                cur.execute(
                    f"SELECT * FROM alert_rule_conditions WHERE rule_id IN ({placeholders}) "
                    f"ORDER BY rule_id, sort_order, id",
                    rule_ids
                )
            conds_by_rule = {}
            for r in cur.fetchall():
                conds_by_rule.setdefault(r["rule_id"], []).append(_parse_condition_row(r))

            if is_pg():
                cur.execute(
                    "SELECT * FROM alert_rule_actions WHERE rule_id = ANY(%s) "
                    "ORDER BY rule_id, sort_order, id",
                    (rule_ids,)
                )
            else:
                placeholders = ",".join("?" * len(rule_ids))
                cur.execute(
                    f"SELECT * FROM alert_rule_actions WHERE rule_id IN ({placeholders}) "
                    f"ORDER BY rule_id, sort_order, id",
                    rule_ids
                )
            acts_by_rule = {}
            for r in cur.fetchall():
                acts_by_rule.setdefault(r["rule_id"], []).append(_parse_action_row(r))

        for rule in rules:
            rule["conditions"] = conds_by_rule.get(rule["id"], [])
            rule["actions"]    = acts_by_rule.get(rule["id"], [])
        return rules
    except Exception as e:
        log.error(f"db_list_rules error: {e}")
        return rules  # rules without conditions/actions is still useful


def db_get_rule(rule_id: int) -> dict | None:
    """Return a single rule with conditions and actions, or None."""
    row = db_query_one("main", "SELECT * FROM alert_rules WHERE id=?", (rule_id,))
    if not row:
        return None
    rule = _rule_row_to_dict(row)
    cond_rows = db_query("main",
                         "SELECT * FROM alert_rule_conditions WHERE rule_id=? ORDER BY sort_order, id",
                         (rule_id,))
    rule["conditions"] = [_parse_condition_row(r) for r in cond_rows]
    act_rows = db_query("main",
                        "SELECT * FROM alert_rule_actions WHERE rule_id=? ORDER BY sort_order, id",
                        (rule_id,))
    rule["actions"] = [_parse_action_row(r) for r in act_rows]
    return rule


# ── Public write functions (called inside _db_enqueue lambdas) ────

def _rule_fields(data: dict, now: float) -> tuple:
    return (
        data["name"],
        1 if data.get("enabled", True) else 0,
        data.get("severity", "warning"),
        data.get("condition_logic", "AND"),
        int(data.get("cooldown_s", 300)),
        int(data.get("trigger_count", 1)),
        int(data.get("recover_count", 1)),
        int(data.get("sort_order", 0)),
        now,
    )


def db_create_rule(data: dict) -> int:
    """Insert a new rule (with conditions + actions). Returns new rule id."""
    now = time.time()
    fields = _rule_fields(data, now) + (now,)  # extra now for created_at
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            placeholders = ",".join([ph] * 10)
            insert_sql = (
                f"INSERT INTO alert_rules (name, enabled, severity, condition_logic, "
                f"cooldown_s, trigger_count, recover_count, sort_order, updated_at, created_at) "
                f"VALUES ({placeholders})"
            )
            if is_pg():
                cur.execute(insert_sql + " RETURNING id", fields)
                rule_id = cur.fetchone()["id"]
            else:
                cur.execute(insert_sql, fields)
                rule_id = cur.lastrowid
            _write_conditions(cur, rule_id, data.get("conditions", []), is_pg())
            _write_actions(cur, rule_id, data.get("actions", []), is_pg())
        return rule_id
    except Exception as e:
        log.error(f"db_create_rule error: {e}")
        return -1


def db_update_rule(rule_id: int, data: dict) -> bool:
    """Replace a rule's fields, conditions, and actions atomically."""
    now = time.time()
    fields = _rule_fields(data, now) + (rule_id,)
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(
                f"UPDATE alert_rules SET name={ph}, enabled={ph}, severity={ph}, "
                f"condition_logic={ph}, cooldown_s={ph}, trigger_count={ph}, recover_count={ph}, "
                f"sort_order={ph}, updated_at={ph} WHERE id={ph}",
                fields
            )
            cur.execute(f"DELETE FROM alert_rule_conditions WHERE rule_id={ph}", (rule_id,))
            cur.execute(f"DELETE FROM alert_rule_actions WHERE rule_id={ph}", (rule_id,))
            _write_conditions(cur, rule_id, data.get("conditions", []), is_pg())
            _write_actions(cur, rule_id, data.get("actions", []), is_pg())
        return True
    except Exception as e:
        log.error(f"db_update_rule error: {e}")
        return False


def db_delete_rule(rule_id: int) -> bool:
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            cur.execute(f"DELETE FROM alert_rule_conditions WHERE rule_id={ph}", (rule_id,))
            cur.execute(f"DELETE FROM alert_rule_actions WHERE rule_id={ph}", (rule_id,))
            cur.execute(f"DELETE FROM alert_rules WHERE id={ph}", (rule_id,))
        return True
    except Exception as e:
        log.error(f"db_delete_rule error: {e}")
        return False


def db_set_rule_enabled(rule_id: int, enabled: bool) -> bool:
    return db_execute("main",
                      "UPDATE alert_rules SET enabled=?, updated_at=? WHERE id=?",
                      (1 if enabled else 0, time.time(), rule_id))


def db_reorder_rules(id_list: list) -> None:
    """Set sort_order for each rule id in id_list (position = index)."""
    try:
        with db_cursor("main") as cur:
            ph = "%s" if is_pg() else "?"
            for i, rule_id in enumerate(id_list):
                cur.execute(f"UPDATE alert_rules SET sort_order={ph} WHERE id={ph}", (i, rule_id))
    except Exception as e:
        log.error(f"db_reorder_rules error: {e}")


# ── Private write helpers (used inside an active cursor) ──────────

def _write_conditions(cur, rule_id: int, conditions: list, pg: bool):
    ph = "%s" if pg else "?"
    for i, c in enumerate(conditions):
        cur.execute(
            f"INSERT INTO alert_rule_conditions (rule_id, field, op, value, sort_order) "
            f"VALUES ({ph},{ph},{ph},{ph},{ph})",
            (rule_id, c["field"], c["op"], str(c.get("value", "")), i)
        )


def _write_actions(cur, rule_id: int, actions: list, pg: bool):
    ph = "%s" if pg else "?"
    for i, a in enumerate(actions):
        cfg = a.get("config", {})
        if not isinstance(cfg, str):
            cfg = json.dumps(cfg)
        cur.execute(
            f"INSERT INTO alert_rule_actions (rule_id, atype, config, sort_order) "
            f"VALUES ({ph},{ph},{ph},{ph})",
            (rule_id, a["atype"], cfg, i)
        )
