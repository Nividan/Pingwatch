"""
db/__init__.py — Re-exports every public symbol from the db sub-modules so
that all existing callers (server.py, state.py, trap_receiver.py, etc.) that
use ``import db; db.X()`` or ``from db import X`` continue to work unchanged.
"""

# backend — backend selection + config
from db.backend     import is_pg, needs_setup

# core — write queues + schema
from db.core        import db_init, db_seed_users, db_seed_alert_profiles, \
                           _db_enqueue, _logs_enqueue, logs_db_init, \
                           shutdown_writers

# persistence — device/sensor save/load + autosave
from db.persistence import (
    db_load, db_save, autosave_loop,
    db_load_anomaly_baselines, db_checkpoint_anomaly_baselines,
    db_reset_anomaly_baseline,
)

# samples — buffered probe writes + history queries
from db.samples     import (
    db_buffer_sample,
    db_flush_samples,
    db_load_history,
    db_load_summary,
    db_load_availability,
    db_clean_samples,
    db_rollup_backfill,
    db_cleanup_impossible_rates,
    db_sample_buffer_stats,
)

# events — flap log, SNMP trap log, sensor error log
from db.events      import (
    db_log_flap,
    db_load_flaps,
    db_auto_resolve_flap,
    db_ack_flap,
    db_ack_flaps_by_sensor,
    db_resolve_flap,
    db_resolve_flaps_by_sensor,
    db_resolve_all_flaps,
    db_has_open_flap,
    db_count_active_flaps,
    db_count_active_flaps_by_severity,
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
    db_add_ldap_user,
    db_add_radius_user,
    db_add_sso_user,
    db_get_user_by_external_id,
    db_update_external_id,
    db_get_user_auth_type,
    db_delete_user,
    db_set_password,
    db_load_settings,
    db_save_settings,
    db_list_dashboards,
    db_get_dashboard,
    db_create_dashboard,
    db_rename_dashboard,
    db_delete_dashboard,
    db_save_dashboard,
    db_reorder_dashboards,
    db_update_profile,
    db_update_own_profile,
    db_update_theme,
    # TOTP helpers
    db_get_totp,
    db_set_totp,
    db_clear_totp,
    # Trusted devices (Remember 2FA)
    db_get_remember_hours,
    db_set_remember_hours,
    db_add_trusted_device,
    db_lookup_trusted_device,
    db_touch_trusted_device,
    db_list_trusted_devices,
    db_revoke_trusted_device,
    db_revoke_trusted_devices,
    db_sweep_expired_trusted_devices,
)

# groups
from db.groups      import (
    db_list_groups,
    db_create_group,
    db_update_group,
    db_delete_group,
    db_update_group_members,
    db_resolve_group_emails,
    db_get_ldap_mapped_groups,
    db_find_group_by_radius,
    db_get_radius_mapped_groups,
    db_get_saml_mapped_groups,
    db_get_oidc_mapped_groups,
)

# sites (Live Map metadata sidecar)
from db.sites       import (
    db_list_sites,
    db_get_site_meta,
    db_upsert_site_meta,
    db_ensure_site_meta,
    db_rename_site_meta,
    db_delete_site_meta,
    db_distinct_site_names,
    db_site_usage,
    KNOWN_KINDS,
)

# audit
from db.audit       import db_log_audit, db_get_audit

# ipam
from db.ipam        import (
    db_list_subnets,
    db_get_subnet,
    db_add_subnet,
    db_rename_subnet,
    db_delete_subnet,
    db_update_subnet,
    db_set_auto_discover,
    db_approve_first_scan,
    db_set_subnet_last_scan,
    db_get_allocations,
    db_upsert_allocation,
    db_clear_allocation,
    db_mark_allocations_stale,
    apply_subnet_scan_results,
    db_set_device_role,
    db_get_device_roles,
    db_update_dns,
    ipam_sync_device_add,
    ipam_sync_device_update,
    ipam_sync_device_delete,
    ipam_sync_subnet_add,
)

# alert profiles (PRTG-style state-trigger system)
from db.alert_profiles import (
    db_list_profiles,
    db_get_profile,
    db_get_profile_for_scope,
    db_save_profile,
    db_delete_profile,
    db_set_profile_enabled,
    db_list_action_templates,
    db_get_action_template,
    db_save_action_template,
    db_delete_action_template,
    db_get_stage_state,
    db_record_stage_fire,
    db_clear_stage_state_for_sensor,
    db_list_active_stage_sessions_for_sensor,
)

# alert events (history + ack/resolve)
from db.alert_events import (
    db_log_event,
    db_list_events,
    db_count_active,
    db_get_event,
    db_ack_event,
    db_resolve_event,
    db_auto_resolve_event,
    db_resolve_all_active,
    db_resolve_events_by_sensor,
    db_has_acked_event,
    db_has_active_event,
)

# licenses
from db.licenses    import (
    db_get_licenses,
    db_get_all_licenses,
    db_add_license,
    db_update_license,
    db_delete_license,
    db_delete_device_licenses,
    db_update_license_status,
    db_license_summary,
)

# reports (templates, schedules, generated history)
from db.reports     import (
    db_list_report_templates,
    db_get_report_template,
    db_create_report_template,
    db_update_report_template,
    db_delete_report_template,
    db_list_report_schedules,
    db_get_report_schedule,
    db_list_schedules_for_template,
    db_create_report_schedule,
    db_update_report_schedule,
    db_set_schedule_enabled,
    db_record_schedule_run,
    db_delete_report_schedule,
    db_list_report_history,
    db_get_report_history,
    db_add_report_history,
    db_update_report_history_delivery,
    db_delete_report_history,
    db_prune_report_history,
)

# backups
from db.backups     import (
    db_get_backup_list,
    db_get_backup_settings,
    db_save_backup_settings,
    db_get_backup_history,
    db_get_backup_run,
    db_save_backup_run,
    db_get_last_successful_config,
    db_delete_backup_run,
    db_ensure_backup_device,
    db_write_config_file,
    db_search_configs,
    encrypt_pw,
    decrypt_pw,
)

__all__ = [
    # backend
    "is_pg", "needs_setup",
    # core
    "db_init", "db_seed_users", "db_seed_alert_profiles", "_db_enqueue",
    "_logs_enqueue", "logs_db_init", "shutdown_writers",
    # persistence
    "db_load", "db_save", "autosave_loop",
    "db_load_anomaly_baselines", "db_checkpoint_anomaly_baselines",
    "db_reset_anomaly_baseline",
    # samples
    "db_buffer_sample", "db_flush_samples",
    "db_load_history", "db_load_summary", "db_load_availability", "db_clean_samples",
    "db_rollup_backfill", "db_cleanup_impossible_rates", "db_sample_buffer_stats",
    # events
    "db_log_flap", "db_load_flaps", "db_auto_resolve_flap", "db_ack_flap", "db_ack_flaps_by_sensor", "db_resolve_flap", "db_resolve_flaps_by_sensor", "db_resolve_all_flaps", "db_has_open_flap", "db_count_active_flaps", "db_count_active_flaps_by_severity",
    "db_log_trap", "db_load_traps", "db_clear_device_traps",
    "db_log_err", "db_load_err_logs", "db_clear_err_logs",
    "db_clear_sensor_err_logs",
    # users & settings
    "db_list_users", "db_add_user", "db_add_ldap_user", "db_add_radius_user",
    "db_add_sso_user", "db_get_user_by_external_id", "db_update_external_id",
    "db_get_user_auth_type",
    "db_delete_user", "db_set_password",
    "db_load_settings", "db_save_settings",
    "db_list_dashboards", "db_get_dashboard", "db_create_dashboard",
    "db_rename_dashboard", "db_delete_dashboard", "db_save_dashboard",
    "db_reorder_dashboards",
    "db_update_profile", "db_update_own_profile",
    # TOTP helpers
    "db_get_totp", "db_set_totp", "db_clear_totp",
    # Trusted devices
    "db_get_remember_hours", "db_set_remember_hours",
    "db_add_trusted_device", "db_lookup_trusted_device", "db_touch_trusted_device",
    "db_list_trusted_devices", "db_revoke_trusted_device",
    "db_revoke_trusted_devices", "db_sweep_expired_trusted_devices",
    # groups
    "db_list_groups", "db_create_group", "db_update_group", "db_delete_group",
    "db_update_group_members", "db_resolve_group_emails", "db_get_ldap_mapped_groups",
    "db_find_group_by_radius", "db_get_radius_mapped_groups",
    "db_get_saml_mapped_groups", "db_get_oidc_mapped_groups",
    # audit
    "db_log_audit", "db_get_audit",
    # ipam
    "db_list_subnets", "db_get_subnet", "db_add_subnet", "db_rename_subnet", "db_delete_subnet",
    "db_update_subnet", "db_set_auto_discover", "db_approve_first_scan", "db_set_subnet_last_scan",
    "db_get_allocations", "db_upsert_allocation", "db_clear_allocation",
    "db_mark_allocations_stale", "apply_subnet_scan_results",
    "db_set_device_role", "db_get_device_roles", "db_update_dns",
    "ipam_sync_device_add", "ipam_sync_device_update",
    "ipam_sync_device_delete", "ipam_sync_subnet_add",
    # alert profiles
    "db_list_profiles", "db_get_profile", "db_get_profile_for_scope",
    "db_save_profile", "db_delete_profile", "db_set_profile_enabled",
    "db_list_action_templates", "db_get_action_template",
    "db_save_action_template", "db_delete_action_template",
    "db_get_stage_state", "db_record_stage_fire",
    "db_clear_stage_state_for_sensor",
    "db_list_active_stage_sessions_for_sensor",
    # alert events
    "db_log_event", "db_list_events", "db_count_active", "db_get_event",
    "db_ack_event", "db_resolve_event", "db_auto_resolve_event",
    "db_resolve_all_active", "db_has_acked_event", "db_has_active_event",
    # reports
    "db_list_report_templates", "db_get_report_template",
    "db_create_report_template", "db_update_report_template", "db_delete_report_template",
    "db_list_report_schedules", "db_get_report_schedule",
    "db_list_schedules_for_template",
    "db_create_report_schedule", "db_update_report_schedule",
    "db_set_schedule_enabled", "db_record_schedule_run", "db_delete_report_schedule",
    "db_list_report_history", "db_get_report_history",
    "db_add_report_history", "db_update_report_history_delivery",
    "db_delete_report_history", "db_prune_report_history",
    # backups
    "db_get_backup_list", "db_get_backup_settings", "db_save_backup_settings",
    "db_get_backup_history", "db_get_backup_run", "db_save_backup_run",
    "db_get_last_successful_config",
    "db_delete_backup_run", "db_ensure_backup_device", "db_write_config_file",
    "db_search_configs", "encrypt_pw", "decrypt_pw",
]
