# PingWatch Managed Upgrade (signed image, file-upload, air-gap friendly)

This is the file-upload alternative to the git `deploy.sh` flow: an admin uploads
a **signed image** in the UI; the server verifies it, snapshots the database,
installs the new release, restarts, and **auto-rolls-back** if the new version
does not come up healthy. No git or internet is needed on the server — the image
is a single file you can carry into an air-gapped site.

A flat (git) install keeps working exactly as before; the managed layout is opt-in.

## Layout

```
<base>/                      install root (immutable scaffolding lives here)
  bootstrap.py               outer supervisor: release selection, probation, rollback
  linux/ windows/            launch scripts the OS service invokes
  current.txt                pointer -> the active release dir name
  upgrade_state.json         upgrade state machine
  update_report.json         outcome of the last upgrade (committed | rolled_back)
  server_health.json         health beacon the supervisor polls during probation
  releases/<version>/        swappable server runtime (server.py, core/, ...)
  db_snapshots/<id>/         pre-upgrade DB snapshot (restored on rollback)
  data/                      persistent state: pingwatch.conf, DBs, certs, logs, backups
```

`bootstrap.py` (Linux systemd `ExecStart`, or invoked by `windows/launcher.pyw`)
resolves `current.txt`, applies any staged swap, runs the release as a child, and
watches `server_health.json`. The server reads `PW_DATA_DIR=<base>/data` so a code
swap never touches persistent state. In a flat checkout `bootstrap.py` is a
transparent pass-through to `./server.py`.

## Release signing keys (hybrid trust)

Images are signed with **Ed25519**. A server accepts an image if its signature
matches **any trusted key**:

- the **vendor key** baked into `core/upgrade.py` (`VENDOR_PUBKEY_HEX`) — the
  project's own key; its private half signs official releases and is never in
  the repo; and
- any **operator keys** a self-hoster registers on their own box with
  `tools/trust_key.py` (stored in `data/trusted_upgrade_keys.json`).

So a cloned/self-hosted server trusts official releases out of the box **and**
can build + install its own images after registering its own public key:

```bash
# on the server (shell access required — deliberately not a web action):
python tools/trust_key.py --add <your-64-hex-pubkey> --label "my build box"
python tools/trust_key.py --list
```

Adding a trusted key requires filesystem access on purpose: a compromised
web-admin session cannot widen who may push code.

Generate a keypair once:

```bash
python - <<'PY'
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s
k = Ed25519PrivateKey.generate()
print("PRIV", k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()).hex())
print("PUB ", k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw).hex())
PY
```

Put the public hex in `core/upgrade.py:RELEASE_PUBKEY_HEX`; store the private hex
outside the repo (e.g. `~/.local/share/pingwatch/secrets/release_ed25519.key` or
`PW_RELEASE_SIGNING_KEY`). **Key rotation** is a two-release migration: ship an
image signed by the OLD key whose code bakes in the NEW public key, deploy it,
then start signing with the NEW key.

## Build an image (build machine)

```bash
PW_RELEASE_SIGNING_KEY=<hex> python tools/build_image.py --out ./out
#   or:  python tools/build_image.py --key /path/to/release_ed25519.key --out ./out
# -> ./out/pingwatch-image-<version>.zip
```

The image contains `manifest.json` (version, `app_version`, `payload_sha256`,
`min_upgrade_from`), a detached `manifest.sig`, and `payload/` (the source tree
minus runtime state). Copy the zip to the target site by whatever means (USB,
SCP, download-then-carry).

## Convert an existing flat install to the managed layout (once)

Stop the service and back up the DB first.

```bash
python tools/convert_to_managed.py --apply          # cross-platform
# or, on Linux:
bash linux/start.sh --convert-managed --apply
```

Run without `--apply` for a dry run that prints the move plan and changes nothing.
Re-running on an already-managed base is a no-op.

## Upgrade

Settings → **Upgrade** → *Upload & Install Image*. The server runs the verify
chain (**signature → payload sha256 → version compatibility → syntax check**),
snapshots the DB, publishes the new release, and restarts into it under a
probation window (~120 s). If it reports healthy it commits; otherwise the
supervisor restores the previous release **and** the DB snapshot automatically.

API (admin, audit-logged):

| Method/Path | Purpose |
|---|---|
| `POST /api/upgrade/image` | upload a signed image (`application/octet-stream`) |
| `GET  /api/upgrade/status` | managed state + last outcome |
| `POST /api/upgrade/rollback` | revert to previous release + DB snapshot |

## Rollback semantics (important)

Because schema migrations run forward on the new release's first boot, a rollback
restores the **pre-upgrade DB snapshot** — which intentionally **discards any data
written during the probation window** (old code may not understand new-schema
rows). The window is deliberately short to minimize this. SQLite restore is an
atomic file swap; PostgreSQL restore replays `pg_dump` via `psql` (slower,
non-atomic — `main` is restored first; high-volume `logs` is best-effort).

## Caveats

- **Dependencies:** an image does not run `pip install`. If a release adds a
  Python dependency, install it on the server (or bundle wheels) before/with the
  upgrade. A missing import is caught by the health gate and triggers auto-rollback.
- **Trust:** uploading an image runs code as the server user. The endpoint is
  admin-only, audit-logged, and signature-gated — keep the private key safe.
- **`bootstrap.py`, `linux/`, `windows/` are base scaffolding** — changed only by
  re-install, never by an in-product upgrade (which swaps only `releases/`).
