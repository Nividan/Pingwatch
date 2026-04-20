"""core/import_parsers — file-format parsers for Bulk Device Import.

Each parser exposes a single top-level function that accepts raw text (or
bytes for XLSX) and returns the *canonical parse result*:

    {
      "devices": [
        {
          "external_id": "prtg:2001",            # optional
          "name":         "router1",              # required
          "host":         "10.0.0.1",             # required
          "group":        "Core Switches",        # optional, defaults applied later
          "snmp_community_default": "public",     # optional
          "snmp_version_default":   "2c",         # optional
          "webhook_url":            "",           # optional
          "sensors": [
            {"name": "ICMP", "stype": "ping"},
            {"name": "Uptime", "stype": "snmp",
             "snmp_oid": "1.3.6.1.2.1.1.3.0", "snmp_community": "public"},
          ],
        },
        ...
      ],
      "errors": [
        {"row": 17, "reason": "missing required field: host"},
      ],
      "mapping_report": {                   # only for PRTG / Zabbix / SW
        "sensors_total":   156,
        "sensors_mapped":  142,
        "sensors_skipped": [
          {"source_type": "wmicpuload", "count": 12,
           "reason": "no PingWatch equivalent (Windows WMI)"},
        ],
      },
    }

The `/api/import/parse` endpoint calls `preview_match()` from
`core.device_importer` on the returned `devices` list to add match_status
/ match_did / match_diff fields for the review UI. Parsers do not need to
do that themselves — identity lookup requires live STATE access and
belongs in the importer.

Parsers MUST NOT raise on bad input; every failure becomes a row in
`errors` so the whole file doesn't blow up on one bad entry.
"""

from __future__ import annotations

from core.import_parsers.json_parser       import parse_json
from core.import_parsers.csv_parser        import parse_csv
from core.import_parsers.prtg_parser       import parse_prtg_xml
from core.import_parsers.zabbix_parser     import parse_zabbix_xml
from core.import_parsers.solarwinds_parser import (
    parse_solarwinds, inspect_solarwinds,
)

# Registry — used by routes/imports.py to dispatch by format.
# Note: SolarWinds is a special case — the route layer calls it directly
# with `column_map` + `fmt`, so it isn't in this single-arg registry.
PARSERS = {
    "json":   parse_json,
    "csv":    parse_csv,
    "prtg":   parse_prtg_xml,
    "zabbix": parse_zabbix_xml,
}

__all__ = ["PARSERS", "parse_json", "parse_csv", "parse_prtg_xml",
           "parse_zabbix_xml", "parse_solarwinds", "inspect_solarwinds"]
