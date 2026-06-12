# PingWatch Remote Probe Agent

Runs sensor probes inside this network and ships results to your central
PingWatch server over outbound HTTPS — nothing inbound is required at the
branch. Assign devices, sensors, or whole sites to this probe in the
server's **Probes** page and they are measured from here.

## Install

This folder was downloaded pre-configured from the server (config.json
already contains the server URL, a one-time enrollment token, and the
server's certificate fingerprint).

**Linux**

```bash
sudo bash install.sh
```

Installs to `/opt/pingwatch-agent` and registers the
`pingwatch-agent` systemd service (auto-restart, starts at boot).
When run in a terminal it detects missing optional capabilities
(`snmpget` for SNMP sensors, paramiko for SSH/SFTP) and offers to
install them. For unattended installs use flags instead of prompts:

```bash
sudo bash install.sh --all-optional     # or --with-snmp / --with-ssh / --no-optional
```

Re-running the installer later is safe — e.g. `--with-ssh` once SSH
sensors get assigned to this probe.

**Windows** (elevated prompt)

```bat
install.bat
```

Registers a Scheduled Task `PingWatchAgent` that runs at boot as SYSTEM.
Offers to `pip install paramiko` for SSH/SFTP sensors; SNMP needs the
net-snmp `snmpget.exe` installed manually (the installer points there).

**Manual / test run**

```bash
python3 agent.py
```

## What happens on first start

1. The agent exchanges the one-time enrollment token for its own
   long-lived probe credential (stored in `agent_state.json`, mode 600).
2. It pulls its sensor list and starts probing on each sensor's interval.
3. Every ~10s it POSTs collected results to the server (immediately when a
   sensor flips up/down). The server does all debounce/threshold/alerting.

## Offline behavior

If the server is unreachable the agent keeps probing and spools results to
`spool.jsonl` (bounded, survives restarts). On reconnect it backfills
oldest-first — history charts on the server stay gapless, and no alert
storm replays incidents that already ended.

## Troubleshooting: "certificate fingerprint mismatch"

The package pins the certificate PingWatch itself serves. If agents reach
the server **through a reverse proxy** (nginx / Let's Encrypt on 443), the
cert they see belongs to the proxy and the pin fails. Fix in
`config.json` + restart:

- Proxy / publicly-trusted cert → `"server_cert_sha256": ""`
  (normal CA validation; survives renewals — the right choice).
- Self-signed cert that legitimately rotated → paste the full
  *"server presented …"* fingerprint from the error into
  `server_cert_sha256`.

Packages downloaded through a proxy now ship with an empty pin
automatically (the server detects `X-Forwarded-*` headers).

## Re-enrolling

If the probe credential is revoked on the server, generate a new
enrollment token (Probes page → Re-enroll), paste it into `config.json` as
`enrollment_token`, and restart the agent. (Or download a fresh package.)

## Optional capabilities

| Sensors      | Needs on this host                                    |
|--------------|-------------------------------------------------------|
| snmp         | `snmpget` binary (net-snmp)                            |
| ssh / sftp   | paramiko                                               |
| vmware       | `pip install pyvmomi` (+ vmware/ package from server)  |

The installers offer snmp/ssh automatically (see above); vmware stays
manual. Everything else is Python 3.8+ stdlib. A declined or missing
capability is never fatal — those sensors just report *"capability
missing on probe"* until it's added (no agent restart needed; the
capability chips on the server's Probes page update within ~5 minutes).

## Files

| File               | Purpose                                          |
|--------------------|--------------------------------------------------|
| `config.json`      | Server URL, enrollment token, cert pin (edit-safe) |
| `agent_state.json` | Probe credential + cached sensor config (auto)   |
| `spool.jsonl`      | Offline result buffer (auto)                     |
| `agent.log`        | Rotating log (2 MB × 3)                          |
