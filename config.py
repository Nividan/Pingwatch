"""
config.py — Shared constants and compiled route patterns.
"""

import os
import re
import platform

PORT           = 7070
BIND           = "0.0.0.0"
SNMP_TRAP_PORT = 162
SYS            = platform.system()

DB_PATH     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pingwatch.db")
SESSION_TTL = 86400   # 24 hours

FRONTEND_DIR     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
CONFIGS_DIR      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "configs")
CERTS_DIR        = os.path.join(os.path.dirname(os.path.abspath(__file__)), "certs")
TLS_PORT_DEFAULT = 8443

# Pre-compiled HTTP route patterns
_RE_DEVICE_LOGS   = re.compile(r'^/api/device/([^/]+)/logs$')
_RE_DEVICE        = re.compile(r'^/api/device/([^/]+)$')
_RE_DEVICE_ACTION = re.compile(r'^/api/device/([^/]+)/(start|stop)$')
_RE_SENSOR        = re.compile(r'^/api/device/([^/]+)/sensor$')
_RE_SENSOR_ACTION = re.compile(r'^/api/device/([^/]+)/sensor/([^/]+)/(start|stop)$')
_RE_SENSOR_ITEM   = re.compile(r'^/api/device/([^/]+)/sensor/([^/]+)$')
_RE_USER            = re.compile(r'^/api/users/([^/]+)$')
_RE_USER_PW         = re.compile(r'^/api/users/([^/]+)/password$')
_RE_ME_PW           = re.compile(r'^/api/me/password$')
_RE_SENSOR_HISTORY  = re.compile(r'^/api/device/([^/]+)/sensor/([^/]+)/history$')
_RE_SENSOR_SUMMARY  = re.compile(r'^/api/device/([^/]+)/sensor/([^/]+)/summary$')
_RE_DEVICE_SCAN     = re.compile(r'^/api/device/([^/]+)/scan$')
_RE_SENSOR_LOGS     = re.compile(r'^/api/device/([^/]+)/sensor/([^/]+)/logs$')
_RE_DB_EXPORT       = re.compile(r'^/api/db/export$')
_RE_DB_IMPORT       = re.compile(r'^/api/db/import$')
_RE_AUDIT           = re.compile(r'^/api/audit$')
_RE_AVAILABILITY    = re.compile(r'^/api/availability$')
_RE_BACKUPS         = re.compile(r'^/api/backups$')
_RE_BACKUP_DEV      = re.compile(r'^/api/backups/([^/]+)$')
_RE_BACKUP_HISTORY  = re.compile(r'^/api/backups/([^/]+)/history$')
_RE_BACKUP_RUN_ID   = re.compile(r'^/api/backups/run/(\d+)$')
_RE_BACKUP_TRIGGER  = re.compile(r'^/api/backups/([^/]+)/run$')
_RE_TLS             = re.compile(r'^/api/tls$')
_RE_TLS_UPLOAD      = re.compile(r'^/api/tls/upload$')
_RE_TLS_GENERATE    = re.compile(r'^/api/tls/generate$')
