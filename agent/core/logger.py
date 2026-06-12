"""Shim for core.logger — route probe logging into the agent's logger.

agent.py configures the root 'pingwatch-agent' logger (rotating file +
console); these children inherit its handlers.
"""
import logging

log         = logging.getLogger("pingwatch-agent.core")
log_sensors = logging.getLogger("pingwatch-agent.probes")
log_audit   = logging.getLogger("pingwatch-agent.audit")
