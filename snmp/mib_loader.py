"""
snmp/mib_loader.py — Lightweight stdlib-only MIB file parser.

Reads standard SMIv1 / SMIv2 .mib (or .txt) files from snmp/mibs/<vendor>/
and upserts trap definitions + enterprise OID mappings into the database.

No external packages required.  Falls back silently if the mibs/ directory
does not exist or contains no parseable files.

Supported constructs:
  OBJECT IDENTIFIER ::= { parent sub-id }  → builds local OID symbol table
  NOTIFICATION-TYPE  (SMIv2)                → generates trap_definitions row
  TRAP-TYPE          (SMIv1)                → generates trap_definitions row
  MODULE-IDENTITY (enterprise OID)          → generates enterprise_oid_map row

Usage:
  from snmp.mib_loader import load_mibs_from_dir
  n = load_mibs_from_dir('/path/to/snmp/mibs')
"""

import os
import re

from db.trap_defs import db_seed_definitions, db_seed_enterprise_map
from core.logger import log


# ── Regex patterns ────────────────────────────────────────────────────────────

# OBJECT IDENTIFIER assignment:  myName OBJECT IDENTIFIER ::= { parent 42 }
_RE_OID_ASSIGN = re.compile(
    r'(\w+)\s+OBJECT\s+IDENTIFIER\s*::=\s*\{\s*(\w+)\s+(\d+)\s*\}',
    re.IGNORECASE
)

# MODULE-IDENTITY:  mibName MODULE-IDENTITY ...  ::= { parent 42 }
_RE_MODULE_IDENTITY = re.compile(
    r'(\w+)\s+MODULE-IDENTITY.*?::=\s*\{\s*(\w+)\s+(\d+)\s*\}',
    re.IGNORECASE | re.DOTALL
)

# NOTIFICATION-TYPE (SMIv2):  trapName NOTIFICATION-TYPE
#   OBJECTS { ... }
#   DESCRIPTION "..."
#   ::= { parent 42 }
_RE_NOTIF = re.compile(
    r'(\w+)\s+NOTIFICATION-TYPE'
    r'(?:\s+OBJECTS\s*\{([^}]*)\})?'
    r'.*?DESCRIPTION\s+"((?:[^"\\]|\\.)*)"'
    r'.*?::=\s*\{\s*(\w+)\s+(\d+)\s*\}',
    re.IGNORECASE | re.DOTALL
)

# TRAP-TYPE (SMIv1):  trapName TRAP-TYPE  ENTERPRISE enterpriseName
#   DESCRIPTION "..."
#   ::= N
_RE_TRAP_TYPE = re.compile(
    r'(\w+)\s+TRAP-TYPE\s+ENTERPRISE\s+(\w+)'
    r'.*?DESCRIPTION\s+"((?:[^"\\]|\\.)*)"'
    r'.*?::=\s*(\d+)',
    re.IGNORECASE | re.DOTALL
)


# ── Well-known root OID seeds ─────────────────────────────────────────────────

_WELL_KNOWN = {
    "iso":                    "1",
    "org":                    "1.3",
    "dod":                    "1.3.6",
    "internet":               "1.3.6.1",
    "mgmt":                   "1.3.6.1.2",
    "mib-2":                  "1.3.6.1.2.1",
    "enterprises":            "1.3.6.1.4.1",
    "private":                "1.3.6.1.4",
    "experimental":           "1.3.6.1.3",
    "snmpV2":                 "1.3.6.1.6",
    "snmpModules":            "1.3.6.1.6.3",
    "snmpMIB":                "1.3.6.1.6.3.1",
    "snmpTraps":              "1.3.6.1.6.3.1.1.5",
    "fortinet":               "1.3.6.1.4.1.12356",
    "fnFortiGateMib":         "1.3.6.1.4.1.12356.101",
    "fgTraps":                "1.3.6.1.4.1.12356.101.2",
    "fgTrapPrefix":           "1.3.6.1.4.1.12356.101.2.0",
    "ciscoMgmt":              "1.3.6.1.4.1.9.9",
    "juniperMIB":             "1.3.6.1.4.1.2636",
    "jnxMibs":                "1.3.6.1.4.1.2636.3",
}


# ── Public API ────────────────────────────────────────────────────────────────

def load_mibs_from_dir(mibs_dir: str) -> int:
    """
    Walk mibs_dir recursively, parse every .mib / .txt file found, and upsert
    all discovered trap definitions + enterprise OID mappings into the DB.
    Returns the total number of trap definitions loaded.
    """
    if not os.path.isdir(mibs_dir):
        return 0

    all_traps      = []
    all_enterprises = []
    total = 0

    for root, _dirs, files in os.walk(mibs_dir):
        for fname in files:
            if not fname.lower().endswith(('.mib', '.txt', '.my')):
                continue
            fpath = os.path.join(root, fname)
            try:
                oid_table, traps, enterprises = _parse_mib_file(fpath)
                all_traps.extend(traps)
                all_enterprises.extend(enterprises)
            except Exception as exc:
                log.warning(f"MIB loader: skipping {fname}: {exc}")

    if all_enterprises:
        db_seed_enterprise_map(all_enterprises)

    if all_traps:
        db_seed_definitions(all_traps)
        total = len(all_traps)

    return total


# ── Internal parser ───────────────────────────────────────────────────────────

def _parse_mib_file(path: str):
    """
    Parse one MIB file.
    Returns (oid_table, trap_defs, enterprise_rows).
      oid_table      : dict  name → dotted OID string
      trap_defs      : list  of trap_definitions-schema dicts
      enterprise_rows: list  of enterprise_oid_map-schema dicts
    """
    with open(path, encoding='utf-8', errors='replace') as fh:
        text = fh.read()

    # Remove single-line and block comments (-- ... )
    text = re.sub(r'--[^\n]*', ' ', text)

    oid_table  = dict(_WELL_KNOWN)

    # ── 1. Infer vendor/product from filename or enclosing directory ──────────
    fname      = os.path.splitext(os.path.basename(path))[0].upper()
    vendor     = _guess_vendor(path)

    # ── 2. Build OID symbol table ─────────────────────────────────────────────
    # Pass 1: direct OBJECT IDENTIFIER assignments
    for m in _RE_OID_ASSIGN.finditer(text):
        name, parent, sub = m.group(1), m.group(2), m.group(3)
        parent_oid = oid_table.get(parent)
        if parent_oid:
            oid_table[name] = f"{parent_oid}.{sub}"

    # Pass 2: MODULE-IDENTITY (often the root of the MIB)
    for m in _RE_MODULE_IDENTITY.finditer(text):
        name, parent, sub = m.group(1), m.group(2), m.group(3)
        parent_oid = oid_table.get(parent)
        if parent_oid:
            oid_table[name] = f"{parent_oid}.{sub}"

    # ── 3. Collect enterprise OID rows ────────────────────────────────────────
    enterprise_rows = []
    for name, oid in oid_table.items():
        if oid.startswith("1.3.6.1.4.1.") and name not in _WELL_KNOWN:
            parts = oid.split(".")
            if len(parts) == 8:          # exactly 7-arc enterprise OID
                enterprise_rows.append({
                    "enterprise_oid": oid,
                    "vendor":         vendor,
                    "product_family": name,
                })

    # ── 4. Parse NOTIFICATION-TYPE (SMIv2) ───────────────────────────────────
    trap_defs = []
    seen_oids = set()

    for m in _RE_NOTIF.finditer(text):
        trap_name   = m.group(1)
        objects_raw = m.group(2) or ""
        description = _clean_desc(m.group(3))
        parent      = m.group(4)
        sub         = m.group(5)

        full_oid = _resolve_oid(parent, sub, oid_table)
        if not full_oid or full_oid in seen_oids:
            continue
        seen_oids.add(full_oid)

        objects = [o.strip() for o in objects_raw.split(',') if o.strip()]

        trap_defs.append(_build_trap_def(
            trap_name, full_oid, description, vendor, objects, oid_table
        ))

    # ── 5. Parse TRAP-TYPE (SMIv1) ───────────────────────────────────────────
    for m in _RE_TRAP_TYPE.finditer(text):
        trap_name   = m.group(1)
        enterprise  = m.group(2)
        description = _clean_desc(m.group(3))
        specific_id = m.group(4)

        enterprise_oid = oid_table.get(enterprise)
        if not enterprise_oid:
            continue
        full_oid = f"{enterprise_oid}.0.{specific_id}"
        if full_oid in seen_oids:
            continue
        seen_oids.add(full_oid)

        trap_defs.append(_build_trap_def(
            trap_name, full_oid, description, vendor, [], oid_table
        ))

    return oid_table, trap_defs, enterprise_rows


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resolve_oid(parent: str, sub: str, oid_table: dict) -> str:
    """Resolve a parent symbol + sub-id to a dotted OID string, or ''."""
    parent_oid = oid_table.get(parent)
    if not parent_oid:
        return ''
    return f"{parent_oid}.{sub}"


def _build_trap_def(
    trap_name: str,
    full_oid: str,
    description: str,
    vendor: str,
    objects: list,
    oid_table: dict,
) -> dict:
    """Build a trap_definitions-schema dict from parsed MIB data."""
    # Infer varbind hints: object names → their OIDs (if resolvable)
    hints = {}
    for obj in objects:
        obj_oid = oid_table.get(obj)
        if obj_oid:
            hints[obj_oid] = obj

    # Infer severity from common keywords in the description
    desc_lower = description.lower()
    if any(w in desc_lower for w in ('fail', 'error', 'down', 'critical', 'crash', 'alarm')):
        severity = 'critical'
    elif any(w in desc_lower for w in ('warn', 'high', 'low', 'exceed', 'threshold')):
        severity = 'warning'
    else:
        severity = 'info'

    return {
        "trap_oid":           full_oid,
        "trap_name":          trap_name,
        "vendor":             vendor,
        "product_family":     "",
        "severity":           severity,
        "category":           "other",
        "probable_cause":     description[:500],
        "description":        description[:1000],
        "recommended_action": "",
        "varbind_hints":      hints,
        "mib_name":           "",
        "source":             "mib",
    }


def _guess_vendor(path: str) -> str:
    """Infer vendor name from directory or filename."""
    parts = path.replace('\\', '/').lower().split('/')
    _VENDOR_KEYWORDS = {
        'fortinet': 'Fortinet',
        'fortigate': 'Fortinet',
        'cisco': 'Cisco',
        'juniper': 'Juniper',
        'apc': 'APC',
        'hpe': 'HPE',
        'aruba': 'Aruba',
        'mikrotik': 'MikroTik',
        'ubiquiti': 'Ubiquiti',
        'paloalto': 'Palo Alto',
        'vmware': 'VMware',
        'net-snmp': 'Net-SNMP',
    }
    for part in reversed(parts):
        for key, name in _VENDOR_KEYWORDS.items():
            if key in part:
                return name
    return 'Unknown'


def _clean_desc(raw: str) -> str:
    """Collapse whitespace and unescape common sequences in DESCRIPTION strings."""
    s = raw.replace('\\n', ' ').replace('\\t', ' ').replace('\\"', '"')
    return re.sub(r'\s+', ' ', s).strip()
