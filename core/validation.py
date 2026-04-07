"""
core/validation.py — Server-side input validation helpers.

These complement (not replace) HTML5 client-side validation. Routes should
call these on data received from the API and let ValueError bubble up to
a generic 400 response.

Example:
    try:
        port = validate_port(body.get("port"))
        host = validate_host(body.get("host"))
    except ValueError as e:
        h._json(400, {"error": str(e)})
        return True
"""

import re

from core.constants import PORT_MIN, PORT_MAX, HOSTNAME_MAX

# Hostname/IP regex: letters, digits, dots, dashes, colons (for IPv6), underscores
_HOST_RE = re.compile(rf'^[a-zA-Z0-9.\-_:]{{1,{HOSTNAME_MAX}}}$')


def validate_port(p) -> int:
    """Coerce p to an int and verify it's in the valid TCP/UDP port range."""
    try:
        p = int(p)
    except (ValueError, TypeError):
        raise ValueError("port must be an integer")
    if not (PORT_MIN <= p <= PORT_MAX):
        raise ValueError(f"port must be between {PORT_MIN} and {PORT_MAX}")
    return p


def validate_host(h) -> str:
    """Strip whitespace and verify the hostname/IP matches a permissive format.

    Does NOT do DNS resolution — that's a network call. Just checks the
    string is plausibly a hostname or IP literal.
    """
    if not isinstance(h, str):
        raise ValueError("host must be a string")
    h = h.strip()
    if not h:
        raise ValueError("host is required")
    if not _HOST_RE.match(h):
        raise ValueError("invalid host format")
    return h


def validate_interval(i, minimum: int = 1, maximum: int = 3600) -> int:
    """Coerce to int and clamp to [minimum, maximum]."""
    try:
        i = int(i)
    except (ValueError, TypeError):
        raise ValueError("interval must be an integer")
    return max(minimum, min(maximum, i))


def validate_timeout(t, minimum: int = 1, maximum: int = 300) -> int:
    """Coerce to int and clamp to [minimum, maximum]."""
    try:
        t = int(t)
    except (ValueError, TypeError):
        raise ValueError("timeout must be an integer")
    return max(minimum, min(maximum, t))


def validate_name(s, max_len: int = 255, label: str = "name") -> str:
    """Strip whitespace and verify a name is non-empty and within length."""
    if not isinstance(s, str):
        raise ValueError(f"{label} must be a string")
    s = s.strip()
    if not s:
        raise ValueError(f"{label} is required")
    if len(s) > max_len:
        raise ValueError(f"{label} too long (max {max_len})")
    return s
