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

# ── PostgreSQL backend (overridden at runtime by pingwatch.conf / env vars) ──
DB_BACKEND   = os.environ.get("PW_DB_BACKEND", "sqlite")
PG_HOST      = os.environ.get("PW_PG_HOST", "localhost")
PG_PORT      = int(os.environ.get("PW_PG_PORT", "5432"))
PG_DATABASE  = os.environ.get("PW_PG_DATABASE", "pingwatch")
PG_USER      = os.environ.get("PW_PG_USER", "pingwatch")
PG_PASSWORD  = os.environ.get("PW_PG_PASSWORD", "")
PG_POOL_MIN  = int(os.environ.get("PW_PG_POOL_MIN", "2"))
PG_POOL_MAX  = int(os.environ.get("PW_PG_POOL_MAX", "20"))

FRONTEND_DIR     = os.path.join(_ROOT, "frontend")
CONFIGS_DIR      = os.path.join(_ROOT, "backup", "configs")
DB_BACKUP_DIR    = os.path.join(_ROOT, "backup", "database")
CERTS_DIR        = os.path.join(_ROOT, "certs")
TLS_PORT_DEFAULT = 8443

# Pre-compiled HTTP route patterns
_RE_DEVICE_LOGS   = re.compile(r'^/api/device/([^/]+)/logs$')
_RE_DEVICE        = re.compile(r'^/api/device/([^/]+)$')
_RE_DEVICE_SIP    = re.compile(r'^/api/device/([^/]+)/secondary-ip$')
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
_RE_TLS_UPLOAD_PFX  = re.compile(r'^/api/tls/upload-pfx$')
_RE_TLS_CSR         = re.compile(r'^/api/tls/csr$')
_RE_TLS_INSTALL     = re.compile(r'^/api/tls/install-signed$')
_RE_SYSLOG_TEST     = re.compile(r'^/api/settings/syslog_test$')
_RE_LOGS            = re.compile(r'^/api/logs/([^/]+)$')
_RE_IPAM_SUBNETS    = re.compile(r'^/api/ipam/subnets$')
_RE_IPAM_SUBNET     = re.compile(r'^/api/ipam/subnets/(\d+)$')
_RE_IPAM_SUBNET_IPS = re.compile(r'^/api/ipam/subnets/(\d+)/ips$')
_RE_IPAM_SUBNET_DNS = re.compile(r'^/api/ipam/subnets/(\d+)/dns/refresh$')
_RE_IPAM_IP         = re.compile(r'^/api/ipam/ips/(\d+)/([^/]+)$')
# Alert profile engine (PRTG-style state-trigger system)
_RE_ALERT_PROFILES      = re.compile(r'^/api/alert/profiles$')
_RE_ALERT_PROFILE_NEW   = re.compile(r'^/api/alert/profile$')
_RE_ALERT_PROFILE       = re.compile(r'^/api/alert/profile/(\d+)$')
_RE_ALERT_PROFILE_ACT   = re.compile(r'^/api/alert/profile/(\d+)/(toggle|test)$')
_RE_ALERT_TEMPLATES     = re.compile(r'^/api/alert/action-templates$')
_RE_ALERT_TEMPLATE_NEW  = re.compile(r'^/api/alert/action-template$')
_RE_ALERT_TEMPLATE      = re.compile(r'^/api/alert/action-template/(\d+)$')
# Alert events
_RE_ALERT_EVENTS        = re.compile(r'^/api/alert/events$')
_RE_ALERT_EVENTS_ACTIVE      = re.compile(r'^/api/alert/events/active$')
_RE_ALERT_EVENTS_RESOLVE_ALL = re.compile(r'^/api/alert/events/resolve-all$')
_RE_ALERT_EVENT         = re.compile(r'^/api/alert/event/(\d+)$')
_RE_ALERT_EVENT_ACT     = re.compile(r'^/api/alert/event/(\d+)/(ack|resolve)$')
# Maintenance windows
_RE_ALERT_WINDOWS   = re.compile(r'^/api/alert/windows$')
_RE_ALERT_WINDOW    = re.compile(r'^/api/alert/window/(\d+)$')
# User groups
_RE_GROUPS             = re.compile(r'^/api/user/groups$')
_RE_GROUP              = re.compile(r'^/api/user/group$')
_RE_GROUP_ITEM         = re.compile(r'^/api/user/group/(\d+)$')
_RE_GROUP_MEMBERS      = re.compile(r'^/api/user/group/(\d+)/members$')
_RE_GROUP_IMPORT_LDAP  = re.compile(r'^/api/user/group/import_ldap$')
# LDAP group operations
_RE_LDAP_SEARCH_GROUPS    = re.compile(r'^/api/ldap/search_groups$')
_RE_LDAP_TEST_USER_GROUPS = re.compile(r'^/api/ldap/test_user_groups$')
# User profiles
_RE_ME_PROFILE      = re.compile(r'^/api/me/profile$')
_RE_USER_PROFILE    = re.compile(r'^/api/users/([^/]+)/profile$')
# Device licenses
_RE_DEVICE_LICENSES   = re.compile(r'^/api/device/([^/]+)/licenses$')
_RE_LICENSE_ITEM      = re.compile(r'^/api/license/(\d+)$')
_RE_LICENSES_ALL      = re.compile(r'^/api/licenses$')
_RE_LICENSES_SUMMARY  = re.compile(r'^/api/licenses/summary$')
_RE_LICENSES_CHECK    = re.compile(r'^/api/licenses/check$')
# Subnet discovery
_RE_DISCOVERY_SCAN     = re.compile(r'^/api/discovery/scan$')
_RE_DISCOVERY_STATUS   = re.compile(r'^/api/discovery/scan/([a-f0-9]{16})$')
_RE_DISCOVERY_BULK_ADD = re.compile(r'^/api/discovery/bulk-add$')
