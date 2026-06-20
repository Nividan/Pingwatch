"""Shim for core.settings — the agent has no app_settings store; every
lookup returns the caller's default."""


def get(key, default=None):
    return default


def load(d):
    pass
