"""Shim for core.config — probes.py only needs the platform name."""
import platform

SYS = platform.system()   # "Windows" | "Linux" | "Darwin"
