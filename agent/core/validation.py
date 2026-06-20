"""Shim for core.validation — probes.py needs the host-format guard.

Keep this regex in sync with the server's core/validation.py: the leading
char must not be a dash because hosts are passed as argv tokens to
ping / snmpget / arp (list-form subprocess — no shell, but "-foo" would be
parsed as a flag: argument injection).
"""
import re

HOSTNAME_MAX = 253
_HOST_RE = re.compile(rf'^(?!-)[a-zA-Z0-9.\-_:]{{1,{HOSTNAME_MAX}}}$')
