#!/usr/bin/env python3
"""
tools/trust_key.py — manage the operator-registered upgrade signing keys on a
PingWatch server (the "self-host" half of the hybrid trust model).

The server always trusts the baked-in vendor key (core/upgrade.VENDOR_PUBKEY_HEX).
This tool lets an OPERATOR add their OWN trusted public key(s) so they can build
and install their own signed images. Keys are stored in the instance's data dir
(data/trusted_upgrade_keys.json). Adding a key requires shell access to the box
ON PURPOSE — it is not a web action, so a compromised admin session cannot widen
who may push code to the server.

Run this ON THE SERVER:

    python tools/trust_key.py --list
    python tools/trust_key.py --add <64-hex-pubkey> --label "my build box"
    python tools/trust_key.py --remove <64-hex-pubkey>

Generate your keypair with tools/gen_signing_key.py on your build machine, then
register the PUBLIC half here. Stdlib only.
"""

import argparse
import json
import os
import sys
import time

# Resolve the data-dir path the server uses, via core.upgrade (which derives it
# from core.config — correct whether run flat or from inside a release).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from core.upgrade import TRUSTED_KEYS_FILE, VENDOR_PUBKEY_HEX
except Exception as e:  # pragma: no cover - misconfigured checkout
    sys.exit("[trust-key] ERROR: could not import core.upgrade (%s). Run from a "
             "PingWatch checkout/release." % type(e).__name__)


def _load():
    try:
        with open(TRUSTED_KEYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else (data.get("keys") or [])
    except FileNotFoundError:
        return []
    except Exception as e:
        sys.exit("[trust-key] ERROR: %s is unreadable (%s)" % (TRUSTED_KEYS_FILE, type(e).__name__))


def _save(entries):
    os.makedirs(os.path.dirname(TRUSTED_KEYS_FILE), exist_ok=True)
    tmp = TRUSTED_KEYS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2)
    os.replace(tmp, TRUSTED_KEYS_FILE)


def _norm(hx):
    return (hx or "").strip().lower()


def cmd_list():
    print("[trust-key] vendor (baked-in, always trusted): %s" % VENDOR_PUBKEY_HEX)
    entries = _load()
    if not entries:
        print("[trust-key] no operator keys registered (%s)" % TRUSTED_KEYS_FILE)
        return 0
    print("[trust-key] operator keys in %s:" % TRUSTED_KEYS_FILE)
    for e in entries:
        if isinstance(e, dict):
            print("    %s  %s" % (e.get("pubkey"), e.get("label") or ""))
        else:
            print("    %s" % e)
    return 0


def cmd_add(pubkey, label):
    hx = _norm(pubkey)
    if len(hx) != 64 or any(c not in "0123456789abcdef" for c in hx):
        sys.exit("[trust-key] ERROR: --add expects a 64-character hex public key.")
    if hx == _norm(VENDOR_PUBKEY_HEX):
        print("[trust-key] that is already the baked-in vendor key — nothing to do.")
        return 0
    entries = _load()
    for e in entries:
        if _norm(e.get("pubkey") if isinstance(e, dict) else e) == hx:
            print("[trust-key] key already registered.")
            return 0
    entries.append({"pubkey": hx, "label": label or "",
                    "added": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    _save(entries)
    print("[trust-key] added. The server will trust images signed by this key "
          "(takes effect on the next upgrade verification).")
    return 0


def cmd_remove(pubkey):
    hx = _norm(pubkey)
    entries = _load()
    kept = [e for e in entries if _norm(e.get("pubkey") if isinstance(e, dict) else e) != hx]
    if len(kept) == len(entries):
        print("[trust-key] key not found among operator keys.")
        return 0
    _save(kept)
    print("[trust-key] removed.")
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description="Manage operator-trusted upgrade signing keys.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="list trusted keys")
    g.add_argument("--add", metavar="PUBKEY", help="register a 64-hex public key")
    g.add_argument("--remove", metavar="PUBKEY", help="unregister a public key")
    ap.add_argument("--label", default="", help="label for --add")
    args = ap.parse_args(argv)
    if args.list:
        return cmd_list()
    if args.add:
        return cmd_add(args.add, args.label)
    return cmd_remove(args.remove)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
