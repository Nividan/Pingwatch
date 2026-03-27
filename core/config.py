"""
config.py — Shared constants and compiled route patterns.
"""

import os
import re
import platform

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PORT           = 7070
BIND           = "0.0.0.0"
SNMP_TRAP_PORT = 162
SYS            = platform.system()

DB_PATH      = os.path.join(_ROOT, "pingwatch.db")
LOGS_DB_PATH = os.path.join(_ROOT, "pingwatch_logs.db")
SESSION_TTL  = 86400   # 24 hours

FRONTEND_DIR     = os.path.join(_ROOT, "frontend")
CONFIGS_DIR      = os.path.join(_ROOT, "backup", "configs")
DB_BACKUP_DIR    = os.path.join(_ROOT, "backup", "database")
CERTS_DIR        = os.path.join(_ROOT, "certs")
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
_RE_DB_EXPORT        = re.compile(r'^/api/db/export$')
_RE_DB_EXPORT_LOGS   = re.compile(r'^/api/db/export/logs$')
_RE_DB_EXPORT_BUNDLE = re.compile(r'^/api/db/export/bundle$')
_RE_DB_IMPORT        = re.compile(r'^/api/db/import$')
_RE_DB_STATS         = re.compile(r'^/api/db/stats$')
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
_RE_SYSLOG_TEST     = re.compile(r'^/api/settings/syslog_test$')
_RE_LOGS            = re.compile(r'^/api/logs/([^/]+)$')
_RE_IPAM_SUBNETS    = re.compile(r'^/api/ipam/subnets$')
_RE_IPAM_SUBNET     = re.compile(r'^/api/ipam/subnets/(\d+)$')
_RE_IPAM_SUBNET_IPS = re.compile(r'^/api/ipam/subnets/(\d+)/ips$')
_RE_IPAM_SUBNET_DNS = re.compile(r'^/api/ipam/subnets/(\d+)/dns/refresh$')
_RE_IPAM_IP         = re.compile(r'^/api/ipam/ips/(\d+)/([^/]+)$')
# Alert rules engine
_RE_ALERT_RULES     = re.compile(r'^/api/alert/rules$')
_RE_ALERT_RULE_NEW  = re.compile(r'^/api/alert/rule$')
_RE_ALERT_RULE      = re.compile(r'^/api/alert/rule/(\d+)$')
_RE_ALERT_RULE_ACT  = re.compile(r'^/api/alert/rule/(\d+)/(toggle|test)$')
# Alert events
_RE_ALERT_EVENTS        = re.compile(r'^/api/alert/events$')
_RE_ALERT_EVENTS_ACTIVE = re.compile(r'^/api/alert/events/active$')
_RE_ALERT_EVENT         = re.compile(r'^/api/alert/event/(\d+)$')
_RE_ALERT_EVENT_ACT     = re.compile(r'^/api/alert/event/(\d+)/(ack|resolve)$')
# Maintenance windows
_RE_ALERT_WINDOWS   = re.compile(r'^/api/alert/windows$')
_RE_ALERT_WINDOW    = re.compile(r'^/api/alert/window/(\d+)$')
