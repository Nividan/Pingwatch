"""smtp_alert.py — stdlib SMTP email alerting for PingWatch."""
import datetime
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from core.logger import log
from core.settings import get as _cfg

# Rate-limit repeated SMTP failures: suppress duplicate errors for 5 minutes
_last_error: dict = {}          # host -> (error_str, timestamp)
_ERROR_SUPPRESS_S = 300         # seconds between identical error logs

# Connection status tracking (in-memory, resets on restart)
_last_ok_ts: float = 0          # timestamp of last successful send / test
_last_err: dict = {'ts': 0.0, 'msg': ''}  # last error

# PingWatch radar logo — base64 SVG (white on transparent, renders on dark/colored bg)
_LOGO_B64 = (
    "PHN2ZyB3aWR0aD0iMjgiIGhlaWdodD0iMjgiIHZpZXdCb3g9IjAgMCAyMCAyMCIgeG1sbnM9"
    "Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48Y2lyY2xlIGN4PSIxMCIgY3k9IjEwIiBy"
    "PSI4LjUiIHN0cm9rZT0icmdiYSgyNTUsMjU1LDI1NSwuNCkiIHN0cm9rZS13aWR0aD0iMSIg"
    "ZmlsbD0ibm9uZSIvPjxjaXJjbGUgY3g9IjEwIiBjeT0iMTAiIHI9IjUiIHN0cm9rZT0icmdi"
    "YSgyNTUsMjU1LDI1NSwuNikiIHN0cm9rZS13aWR0aD0iMSIgZmlsbD0ibm9uZSIvPjxjaXJj"
    "bGUgY3g9IjEwIiBjeT0iMTAiIHI9IjIiIGZpbGw9IndoaXRlIiBvcGFjaXR5PSIuOSIvPjxs"
    "aW5lIHgxPSIxLjUiIHkxPSIxMCIgeDI9IjUiIHkyPSIxMCIgc3Ryb2tlPSJ3aGl0ZSIgc3Ry"
    "b2tlLXdpZHRoPSIxLjIiIG9wYWNpdHk9Ii43Ii8+PGxpbmUgeDE9IjE1IiB5MT0iMTAiIHgy"
    "PSIxOC41IiB5Mj0iMTAiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0aD0iMS4yIiBvcGFj"
    "aXR5PSIuNyIvPjxsaW5lIHgxPSIxMCIgeTE9IjEuNSIgeDI9IjEwIiB5Mj0iNSIgc3Ryb2tl"
    "PSJ3aGl0ZSIgc3Ryb2tlLXdpZHRoPSIxLjIiIG9wYWNpdHk9Ii43Ii8+PGxpbmUgeDE9IjEw"
    "IiB5MT0iMTUiIHgyPSIxMCIgeTI9IjE4LjUiIHN0cm9rZT0id2hpdGUiIHN0cm9rZS13aWR0"
    "aD0iMS4yIiBvcGFjaXR5PSIuNyIvPjwvc3ZnPg=="
)


def _build_msg(subject, body, from_addr, to_addr, html=None):
    """Build a MIME message. If html is provided, sends multipart/alternative."""
    if html:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From']    = from_addr
        msg['To']      = to_addr
        msg.attach(MIMEText(body, 'plain'))
        msg.attach(MIMEText(html, 'html'))
    else:
        msg = MIMEMultipart()
        msg['Subject'] = subject
        msg['From']    = from_addr
        msg['To']      = to_addr
        msg.attach(MIMEText(body, 'plain'))
    return msg


def _status_style(event_type: str, severity: str):
    """Return (banner_color, emoji, label) based on event type / severity."""
    et = (event_type or '').lower()
    sv = (severity   or '').lower()
    if et == 'recovered' or sv == 'recovery':
        return '#1a7a4a', '\U0001f7e2', 'RECOVERY'    # green
    if et == 'down' or sv == 'critical':
        return '#c0392b', '\U0001f534', 'DOWN'         # red
    if sv == 'warning':
        return '#d68910', '\U0001f7e0', 'WARNING'      # orange
    return '#2c6fad',   '\U0001f535', 'INFO'           # blue


def _fmt_duration(seconds) -> str:
    """Format a duration in seconds as a human-readable string."""
    if seconds is None:
        return ''
    s = int(seconds)
    if s < 60:   return f"{s}s"
    if s < 3600: return f"{s // 60}m {s % 60}s"
    h = s // 3600; m = (s % 3600) // 60
    return f"{h}h {m}m"


def _fmt_ts(ts_str: str) -> str:
    """Convert ISO timestamp (e.g. '2026-04-05T16:42:31Z') to 'DD-MM-YYYY HH:MM:SS'."""
    if not ts_str:
        return ''
    try:
        s = str(ts_str).replace('Z', '+00:00')
        dt = datetime.datetime.fromisoformat(s)
        return dt.strftime('%d-%m-%Y %H:%M:%S')
    except Exception:
        return str(ts_str)


def _build_alert_html(rows: list, event_type: str, severity: str,
                      title_device: str, title_sensor: str,
                      logo: bool = True, company: str = 'PingWatch') -> str:
    """Render a clean HTML email body. rows = list of (label, value) tuples."""
    color, _emoji, label = _status_style(event_type, severity)
    table_rows = ''.join(
        f'<tr style="border-bottom:1px solid #e8e8e8">'
        f'<td style="color:#888;width:80px;padding:7px 4px;font-size:13px">{lbl}</td>'
        f'<td style="padding:7px 4px;font-size:13px;color:#222">{val}</td>'
        f'</tr>'
        for lbl, val in rows
    )
    sev_badge = (
        f'<span style="background:{color};color:#fff;padding:2px 9px;'
        f'border-radius:4px;font-size:11px;font-weight:700">{severity.upper()}</span>'
    )
    # Replace severity row value with badge
    table_rows = table_rows.replace(
        f'<td style="padding:7px 4px;font-size:13px;color:#222">{severity}</td>',
        f'<td style="padding:7px 4px">{sev_badge}</td>'
    )
    # Branding bar (logo + company name)
    _co = _safe(company) if company else 'PingWatch'
    if logo:
        branding = (
            f'<tr><td style="background:#141b24;padding:12px 24px">'
            f'<img src="data:image/svg+xml;base64,{_LOGO_B64}" width="24" height="24" '
            f'alt="" style="vertical-align:middle;display:inline-block"/>'
            f'<span style="color:#fff;font-size:15px;font-weight:600;margin-left:8px;'
            f'vertical-align:middle;letter-spacing:.3px">{_co}</span>'
            f'</td></tr>'
        )
    else:
        branding = ''
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f0f0f0;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f0f0;padding:24px 0">
<tr><td align="center">
<table width="520" cellpadding="0" cellspacing="0"
       style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 10px rgba(0,0,0,.13)">
  {branding}
  <tr><td style="background:{color};padding:18px 24px">
    <div style="font-size:26px;display:inline-block;vertical-align:middle">{_emoji}</div>
    <span style="color:#fff;font-size:20px;font-weight:700;margin-left:10px;vertical-align:middle">{label}</span>
    <div style="color:rgba(255,255,255,.82);font-size:12px;margin-top:5px">
      {title_device} &nbsp;/&nbsp; {title_sensor}
    </div>
  </td></tr>
  <tr><td style="padding:18px 24px 8px">
    <table width="100%" cellpadding="0" cellspacing="0">{table_rows}</table>
  </td></tr>
  <tr><td style="background:#f8f8f8;padding:10px 24px;border-top:1px solid #e8e8e8">
    <span style="font-size:11px;color:#aaa">{_co} &nbsp;·&nbsp; Alert Engine</span>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""


def _connect(host, port, tls, user, password):
    """Return an authenticated smtplib connection or raise."""
    if tls == 'ssl':
        srv = smtplib.SMTP_SSL(host, int(port), timeout=10)
    else:
        srv = smtplib.SMTP(host, int(port), timeout=10)
        if tls == 'starttls':
            try:
                srv.starttls()
            except Exception:
                srv.quit()
                raise
    if user:
        srv.login(user, password)
    return srv


def _safe(v):
    """Strip CR/LF from user-controlled values to prevent email header injection."""
    return str(v or '').replace('\r', '').replace('\n', ' ')


def send_rule_email(to_addrs: str, subject_tpl: str, body_tpl: str, ctx: dict):
    """Send an alert rule email. Called from alert_engine.py.

    to_addrs    — comma-separated recipient list
    subject_tpl — subject with {placeholder} tokens (keys from ctx dict)
    body_tpl    — body with {placeholder} tokens; empty → auto-generated
    ctx         — event context dict: dname, sname, stype, host, ts, detail,
                  severity, event_type, direction, etc.
    """
    from db.backups import decrypt_pw as _dec_pw
    host      = _cfg('smtp_host', '')
    port      = _cfg('smtp_port', 587)
    tls       = _cfg('smtp_tls',  'starttls')
    user      = _cfg('smtp_user', '')
    password  = _dec_pw(_cfg('smtp_pass', ''))
    from_addr = _cfg('smtp_from', '')
    if not (host and from_addr and to_addrs.strip()):
        log.warning("Alert rule email skipped — SMTP not configured")
        return

    # Resolve {placeholder} tokens safely
    def _fmt(tpl):
        try:
            return tpl.format(**{k: _safe(str(v)) for k, v in ctx.items()})
        except (KeyError, ValueError):
            return tpl

    event_type = _safe(ctx.get('event_type', ''))
    severity   = _safe(ctx.get('severity',   ''))
    dname      = _safe(ctx.get('dname',      ''))
    sname      = _safe(ctx.get('sname',      ''))
    _c, emoji, _lbl = _status_style(event_type, severity)

    if subject_tpl:
        subject = _fmt(subject_tpl)
    else:
        subject = f"[PingWatch] {emoji} {severity.upper()} — {dname}/{sname}"

    if body_tpl:
        body = _fmt(body_tpl)
        html = None
    else:
        rows = [
            ('Event',    event_type),
            ('Device',   dname),
            ('Sensor',   f"{sname} ({_safe(ctx.get('stype', ''))})"),
            ('Host',     _safe(ctx.get('host',   ''))),
            ('Severity', severity),
            ('Time',     _fmt_ts(ctx.get('ts', ''))),
        ]
        _dur = _fmt_duration(ctx.get('duration_s'))
        if _dur:
            rows.append(('Duration', _dur))
        rows.append(('Detail', _safe(ctx.get('detail', ''))))
        body = '\n'.join(f"{lbl:<8}: {val}" for lbl, val in rows)
        _logo = str(_cfg('email_logo', '1')) == '1'
        _company = _cfg('email_company_name', '') or 'PingWatch'
        html = _build_alert_html(rows, event_type, severity, dname, sname,
                                 logo=_logo, company=_company)

    recipients = [r.strip() for r in to_addrs.split(',') if r.strip()]
    srv = None
    try:
        srv = _connect(host, port, tls, user, password)
        for rcpt in recipients:
            srv.sendmail(from_addr, [rcpt],
                         _build_msg(subject, body, from_addr, rcpt, html).as_string())
        srv.quit(); srv = None
        _last_error.pop(host, None)
        global _last_ok_ts; _last_ok_ts = time.time()
        log.info(f"Rule alert email sent to {to_addrs}: {subject[:60]}")
    except Exception as e:
        err_str = str(e)
        now = time.monotonic()
        last_err, last_ts = _last_error.get(host, (None, 0))
        if err_str != last_err or (now - last_ts) >= _ERROR_SUPPRESS_S:
            log.error(f"Rule alert SMTP failed (host={host}:{port}): {e}")
            _last_error[host] = (err_str, now)
        global _last_err; _last_err = {'ts': time.time(), 'msg': str(e)[:200]}
    finally:
        if srv:
            try: srv.quit()
            except Exception: pass


def test_smtp(cfg):
    """Test SMTP with provided config dict. Returns (ok:bool, msg:str)."""
    global _last_ok_ts, _last_err
    srv = None
    try:
        srv = _connect(
            cfg['host'], cfg.get('port', 587), cfg.get('tls', 'starttls'),
            cfg.get('user', ''), cfg.get('password', '')
        )
        from_addr = cfg.get('from_addr', 'pingwatch@test')
        to_addr   = cfg.get('to_addr', from_addr)
        _logo = str(_cfg('email_logo', '1')) == '1'
        _company = _cfg('email_company_name', '') or 'PingWatch'
        subject   = f'[{_company}] SMTP test \u2014 connection OK'
        body      = f'This is a test email from {_company} alert system.'
        rows = [
            ('Status', 'SMTP connection successful'),
            ('From',   from_addr),
            ('To',     to_addr),
            ('Time',   _fmt_ts(datetime.datetime.now(datetime.timezone.utc).isoformat())),
        ]
        html = _build_alert_html(rows, 'info', 'info', 'SMTP Test', 'Connection OK',
                                 logo=_logo, company=_company)
        srv.sendmail(from_addr, [to_addr],
                     _build_msg(subject, body, from_addr, to_addr, html).as_string())
        srv.quit(); srv = None
        _last_ok_ts = time.time()
        return True, 'Test email sent successfully.'
    except Exception as e:
        _last_err = {'ts': time.time(), 'msg': str(e)[:200]}
        return False, str(e)
    finally:
        if srv:
            try: srv.quit()
            except Exception: pass


def get_smtp_status() -> dict:
    """Return connection status dict for the Settings API."""
    host = str(_cfg('smtp_host', '')).strip()
    if not host:
        state = 'unconfigured'
    elif _last_err['ts'] and (not _last_ok_ts or _last_err['ts'] > _last_ok_ts):
        state = 'error'
    elif _last_ok_ts:
        state = 'ok'
    else:
        state = 'configured'   # host is set but nothing has been sent yet
    return {
        'state':        state,
        'last_ok_ts':   _last_ok_ts or None,
        'last_err_ts':  _last_err['ts'] or None,
        'last_err_msg': _last_err['msg'],
    }
