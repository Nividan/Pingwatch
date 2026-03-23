"""
db/__init__.py — Re-exports every public symbol from the db sub-modules so
that all existing callers (server.py, state.py, trap_receiver.py, etc.) that
use ``import db; db.X()`` or ``from db import X`` continue to work unchanged.
"""

# core — write queue + schema
from db.core        import db_init, db_seed_users, _db_enqueue

# persistence — device/sensor save/load + autosave
from db.persistence import db_load, db_save, autosave_loop

# samples — buffered probe writes + history queries
from db.samples     import (
    db_buffer_sample,
    db_flush_samples,
    db_load_history,
    db_load_summary,
    db_load_availability,
    db_clean_samples,
)

# events — flap log, SNMP trap log, sensor error log
from db.events      import (
    db_log_flap,
    db_load_flaps,
    db_log_trap,
    db_load_traps,
    db_clear_device_traps,
    db_log_err,
    db_load_err_logs,
    db_clear_err_logs,
    db_clear_sensor_err_logs,
)

# users & settings
from db.users       import (
    db_list_users,
    db_add_user,
    db_delete_user,
    db_set_password,
    db_load_settings,
    db_save_settings,
    db_get_dashboard,
    db_save_dashboard,
)

# audit
from db.audit       import db_log_audit, db_get_audit

# ipam
from db.ipam        import (
    db_list_subnets,
    db_get_subnet,
    db_add_subnet,
    db_delete_subnet,
    db_get_allocations,
    db_upsert_allocation,
    db_clear_allocation,
)

# backups
from db.backups     import (
    db_get_backup_list,
    db_get_backup_settings,
    db_save_backup_settings,
    db_get_backup_history,
    db_get_backup_run,
    db_save_backup_run,
    db_delete_backup_run,
    db_ensure_backup_device,
    db_write_config_file,
    encrypt_pw,
    decrypt_pw,
)

__all__ = [
    # core
    "db_init", "db_seed_users", "_db_enqueue",
    # persistence
    "db_load", "db_save", "autosave_loop",
    # samples
    "db_buffer_sample", "db_flush_samples",
    "db_load_history", "db_load_summary", "db_load_availability", "db_clean_samples",
    # events
    "db_log_flap", "db_load_flaps",
    "db_log_trap", "db_load_traps", "db_clear_device_traps",
    "db_log_err", "db_load_err_logs", "db_clear_err_logs",
    "db_clear_sensor_err_logs",
    # users & settings
    "db_list_users", "db_add_user", "db_delete_user", "db_set_password",
    "db_load_settings", "db_save_settings",
    "db_get_dashboard", "db_save_dashboard",
    # audit
    "db_log_audit", "db_get_audit",
    # ipam
    "db_list_subnets", "db_get_subnet", "db_add_subnet", "db_delete_subnet",
    "db_get_allocations", "db_upsert_allocation", "db_clear_allocation",
    # backups
    "db_get_backup_list", "db_get_backup_settings", "db_save_backup_settings",
    "db_get_backup_history", "db_get_backup_run", "db_save_backup_run",
    "db_delete_backup_run", "db_ensure_backup_device", "db_write_config_file",
    "encrypt_pw", "decrypt_pw",
]
