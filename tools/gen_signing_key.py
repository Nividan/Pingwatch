#!/usr/bin/env python3
"""
tools/gen_signing_key.py — generate the Ed25519 release signing keypair used to
sign PingWatch upgrade images.

Run this on your BUILD machine (the box where you run tools/build_image.py), NOT
on the server. It writes the PRIVATE key to a file OUTSIDE the repo (so it can
never be committed) and prints the PUBLIC key, which you paste into
core/upgrade.py:RELEASE_PUBKEY_HEX.

    python tools/gen_signing_key.py                 # default key path, refuses to overwrite
    python tools/gen_signing_key.py --out PATH       # choose where the private key goes
    python tools/gen_signing_key.py --force          # overwrite an existing key (DANGER)

The private key is the ONLY thing that can authorize code to run on your servers.
Keep it secret, back it up offline, and never paste it anywhere. If it leaks,
generate a new one and roll it out (key rotation = one signed upgrade; see
docs/UPGRADE.md).
"""

import argparse
import os
import sys


def _default_key_path():
    # A dedicated dir in the user's home, outside any repo checkout.
    return os.path.join(os.path.expanduser("~"), ".pingwatch-keys", "release_ed25519.key")


def main(argv):
    ap = argparse.ArgumentParser(description="Generate the PingWatch release signing keypair.")
    ap.add_argument("--out", default=_default_key_path(),
                    help="path for the PRIVATE key file (default: ~/.pingwatch-keys/release_ed25519.key)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing key file (this invalidates images signed with the old key)")
    args = ap.parse_args(argv)

    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization as s
    except ImportError:
        sys.exit("[gen-key] ERROR: the 'cryptography' package is required "
                 "(pip install 'cryptography>=41.0.0').")

    out = os.path.abspath(args.out)
    if os.path.exists(out) and not args.force:
        sys.exit(f"[gen-key] ERROR: {out} already exists. Refusing to overwrite a key "
                 f"that may be in use. Pass --force only if you are sure.")

    priv = Ed25519PrivateKey.generate()
    priv_hex = priv.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex()
    pub_hex = priv.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex()

    os.makedirs(os.path.dirname(out), exist_ok=True)
    # Create with owner-only perms from the start where the OS supports it.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(out, flags, 0o600)
    try:
        os.write(fd, (priv_hex + "\n").encode("ascii"))
    finally:
        os.close(fd)
    try:
        os.chmod(out, 0o600)   # no-op semantics on Windows, harmless
    except OSError:
        pass

    print("[gen-key] PRIVATE key written (keep secret, back up offline):")
    print("           " + out)
    print()
    print("[gen-key] PUBLIC key — paste this as RELEASE_PUBKEY_HEX in core/upgrade.py:")
    print()
    print("    " + pub_hex)
    print()
    print("[gen-key] Build images with:")
    print(f"    PW_RELEASE_SIGNING_KEY=$(cat {out})  python tools/build_image.py")
    print(f"    # or:  python tools/build_image.py --key {out}")
    print()
    print("[gen-key] As the VENDOR, paste the public key into core/upgrade.py:VENDOR_PUBKEY_HEX.")
    print("[gen-key] As a SELF-HOSTER, register it on your server instead (no source edit):")
    print(f"    python tools/trust_key.py --add {pub_hex}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
