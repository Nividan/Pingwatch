"""smtp_alert.py — stdlib SMTP email alerting for PingWatch."""
import base64
import datetime
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from core.logger import log
from core.settings import get as _cfg

# Rate-limit repeated SMTP failures: suppress duplicate errors for 5 minutes
_last_error: dict = {}          # host -> (error_str, timestamp)
_ERROR_SUPPRESS_S = 300         # seconds between identical error logs

# Connection status tracking (in-memory, resets on restart)
_last_ok_ts: float = 0          # timestamp of last successful send / test
_last_err: dict = {'ts': 0.0, 'msg': ''}  # last error

# PingWatch radar logo — 28x28 PNG (white on transparent, renders on dark/colored bg)
_LOGO_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAABDklEQVR4nN2WTQqEMAyFBYVZ"
    "9gCzc6Pg1oXbnqEewAN4h7nMHMH7dVJ4hRAj4+gQwcUHEtM+8tK/IsZYWHJ04ALuJ/gkeib"
    "YI/ZXwZLwRCA6wjFBh1hATnlWsMFkboelDrnNUcFk13CgTwPG/iTYbIg9YN0EPGKaqFrpVs+"
    "CEk8CM9ESFWgRm5T8oPVUE/RKz16YfMuRFjmyp36PoKxu+iLGRWWlK6fkoLSnOtGzeYdYZh"
    "Y97aLYp9rK5HZ6pbqaGEGtVMltdFGs2PyxKGQ7KzFpEnqDUfyrhK2reS8TNLf0skVjvi2yja"
    "Yb3/xoS5ge3nzFml1PvFLtAtY4fQHznpo9MSQmjyiN+75LTwt+AKhpaURxnEx6AAAAAElFTk"
    "SuQmCC"
)


def _resolve_logo():
    """Return (image_bytes, mime_subtype) for the email logo.

    Uses custom uploaded logo if available, otherwise falls back to built-in default.
    """
    custom = _cfg('email_logo_data', '')
    if custom:
        # Custom logo stored as data URI: "data:image/png;base64,..."
        try:
            header, b64 = custom.split(',', 1)
            img_bytes = base64.b64decode(b64)
            if 'svg' in header:
                return img_bytes, 'svg+xml'
            elif 'jpeg' in header or 'jpg' in header:
                return img_bytes, 'jpeg'
            elif 'gif' in header:
                return img_bytes, 'gif'
            return img_bytes, 'png'
        except Exception:
            pass  # fall through to default
    return base64.b64decode(_LOGO_B64), 'png'


def _build_msg(subject, body, from_addr, to_addr, html=None, logo=False):
    """Build a MIME message with optional inline logo via CID attachment."""
    if html:
        alt = MIMEMultipart('alternative')
        alt.attach(MIMEText(body, 'plain'))
        alt.attach(MIMEText(html, 'html'))
        if logo:
            # Wrap in multipart/related so CID image resolves
            msg = MIMEMultipart('related')
            msg.attach(alt)
            img_data, img_sub = _resolve_logo()
            img = MIMEImage(img_data, _subtype=img_sub)
            img.add_header('Content-ID', '<pwlogo>')
            img.add_header('Content-Disposition', 'inline', filename='logo.png')
            msg.attach(img)
        else:
            msg = alt
        msg['Subject'] = subject
        msg['From']    = from_addr
        msg['To']      = to_addr
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


def _detail_bg(severity: str) -> str:
    """Return tinted background color for the detail callout box."""
    sv = (severity or '').lower()
    if sv in ('critical', 'down'):
        return '#fef2f2'
    if sv == 'warning':
        return '#fef9f0'
    if sv == 'recovery':
        return '#f0f9f4'
    return '#f0f4fe'


def _html_logo_section(logo: bool, company: str) -> str:
    """Render the hero logo section — large centered logo + company name."""
    if not logo:
        return ''
    _co = _safe(company) if company else 'PingWatch'
    name_html = (
        f'<div style="margin-top:8px;font-size:14px;color:#6b7280;'
        f'font-family:Arial,Helvetica,sans-serif">{_co}</div>'
    ) if company else ''
    return (
        f'<tr><td align="center" style="padding:24px 24px 16px;background:#ffffff">'
        f'<img src="cid:pwlogo" width="180" height="60" '
        f'alt="{_co}" style="display:block;max-width:180px;max-height:60px;'
        f'width:auto;height:auto;margin:0 auto"/>'
        f'{name_html}'
        f'</td></tr>'
    )


def _html_status_banner(color: str, emoji: str, label: str, ts_str: str) -> str:
    """Render the colored status banner with timestamp."""
    return (
        f'<tr><td style="background:{color};padding:18px 24px" bgcolor="{color}">'
        f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
        f'<td style="color:#ffffff;font-size:22px;font-weight:700">'
        f'<span style="font-size:26px;vertical-align:middle">{emoji}</span>'
        f'&nbsp; {label}</td>'
        f'<td align="right" style="color:rgba(255,255,255,.8);font-size:12px;'
        f'vertical-align:middle">{ts_str}</td>'
        f'</tr></table></td></tr>'
    )


def _html_breadcrumb(ctx: dict) -> str:
    """Render the Group > Device > Sensor (Type) breadcrumb path."""
    if not ctx:
        return ''
    parts = []
    grp = _safe(ctx.get('grp', ''))
    if grp:
        parts.append(grp)
    parts.append(_safe(ctx.get('dname', '')))
    sname = _safe(ctx.get('sname', ''))
    stype = _safe(ctx.get('stype', ''))
    parts.append(f'{sname} ({stype})' if stype else sname)
    sep = ' <span style="color:#bbb;margin:0 6px">&rsaquo;</span> '
    crumb = sep.join(f'<span style="color:#555">{p}</span>' for p in parts if p)
    return (
        f'<tr><td style="background:#f8f9fa;padding:10px 24px;'
        f'border-bottom:1px solid #e8e8e8;font-size:12px;'
        f'font-family:Arial,Helvetica,sans-serif">{crumb}</td></tr>'
    )


def _html_detail_box(detail: str, color: str, severity: str) -> str:
    """Render the highlighted detail/message callout box."""
    if not detail:
        return ''
    bg = _detail_bg(severity)
    return (
        f'<tr><td style="padding:20px 24px 4px">'
        f'<table width="100%" cellpadding="0" cellspacing="0" '
        f'style="border-left:4px solid {color};border-radius:0 4px 4px 0">'
        f'<tr><td style="background:{bg};padding:14px 16px">'
        f'<div style="font-size:10px;font-weight:700;color:#888;'
        f'text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px">'
        f'Last Message</div>'
        f'<div style="font-size:13px;color:#333;line-height:1.5;'
        f'word-break:break-word">{_safe(detail)}</div>'
        f'</td></tr></table></td></tr>'
    )


def _html_stat_row(label: str, value: str, odd: bool) -> str:
    """Render a single stats grid row with alternating background."""
    bg = '#ffffff' if odd else '#f8f9fa'
    return (
        f'<tr><td style="background:{bg};padding:7px 12px;font-size:12px;'
        f'color:#888;width:120px;border-bottom:1px solid #f0f0f0">{label}</td>'
        f'<td style="background:{bg};padding:7px 12px;font-size:12px;'
        f'color:#222;border-bottom:1px solid #f0f0f0">{value}</td></tr>'
    )


def _html_section_hdr(title: str) -> str:
    """Render a section header row in the stats grid."""
    return (
        f'<tr><td colspan="2" style="padding:12px 12px 4px;font-size:10px;'
        f'font-weight:700;color:#888;text-transform:uppercase;letter-spacing:.5px;'
        f'border-bottom:1px solid #e0e0e0">{title}</td></tr>'
    )


def _html_stats_grid(ctx: dict) -> str:
    """Render the full monitoring stats grid with section headers."""
    if not ctx:
        return ''
    html = ''
    ri = 0   # row index for alternating colors

    # — Sensor Details —
    html += _html_section_hdr('Sensor Details')
    for lbl, val in [
        ('Host',     _safe(ctx.get('host', '')) or '\u2014'),
        ('Type',     _safe(ctx.get('stype', '')) or '\u2014'),
        ('Group',    _safe(ctx.get('grp', '')) or '\u2014'),
        ('Interval', f"{ctx['interval']}s" if ctx.get('interval') else '\u2014'),
    ]:
        html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1

    # — Performance —
    html += _html_section_hdr('Performance')
    ms = ctx.get('ms')
    perf = [
        ('Latency',     f'{ms:.1f} ms' if ms is not None else '\u2014'),
        ('Packet Loss', f"{ctx.get('loss_pct', 0)}%"),
    ]
    dur = _fmt_duration(ctx.get('duration_s'))
    if dur:
        perf.append(('Downtime', dur))
    for lbl, val in perf:
        html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1

    # — Thresholds (only if any are configured) —
    thr = []
    if ctx.get('warn_ms'):
        thr.append(('Warn Latency', f"&gt; {ctx['warn_ms']} ms"))
    if ctx.get('crit_ms'):
        thr.append(('Crit Latency', f"&gt; {ctx['crit_ms']} ms"))
    if ctx.get('loss_warn_pct'):
        thr.append(('Warn Loss', f"&gt; {ctx['loss_warn_pct']}%"))
    if ctx.get('loss_crit_pct'):
        thr.append(('Crit Loss', f"&gt; {ctx['loss_crit_pct']}%"))
    if thr:
        html += _html_section_hdr('Thresholds')
        for lbl, val in thr:
            html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1

    # — Statistics (only if probes have run) —
    stat = []
    if ctx.get('uptime_pct') is not None:
        stat.append(('Uptime', f"{ctx['uptime_pct']}%"))
    if ctx.get('total'):
        stat.append(('Probes', f"{ctx.get('success', 0):,} / {ctx['total']:,}"))
    if ctx.get('avg_ms') is not None:
        stat.append(('Avg Latency', f"{ctx['avg_ms']:.1f} ms"))
    if ctx.get('min_ms') is not None and ctx.get('max_ms') is not None:
        stat.append(('Min / Max', f"{ctx['min_ms']:.1f} / {ctx['max_ms']:.1f} ms"))
    if stat:
        html += _html_section_hdr('Statistics')
        for lbl, val in stat:
            html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1

    return (
        f'<tr><td style="padding:8px 24px 16px">'
        f'<table width="100%" cellpadding="0" cellspacing="0">'
        f'{html}</table></td></tr>'
    )


def _html_legacy_rows(rows: list) -> str:
    """Render simple key-value rows (backward compat for test_smtp without ctx)."""
    tr = ''
    for i, (lbl, val) in enumerate(rows):
        bg = '#ffffff' if i % 2 == 0 else '#f8f9fa'
        tr += (
            f'<tr><td style="background:{bg};color:#888;width:100px;'
            f'padding:7px 12px;font-size:13px;border-bottom:1px solid #f0f0f0">{lbl}</td>'
            f'<td style="background:{bg};padding:7px 12px;font-size:13px;'
            f'color:#222;border-bottom:1px solid #f0f0f0">{val}</td></tr>'
        )
    return (
        f'<tr><td style="padding:16px 24px">'
        f'<table width="100%" cellpadding="0" cellspacing="0">'
        f'{tr}</table></td></tr>'
    )


def _html_footer(company: str) -> str:
    """Render the email footer row."""
    _co = _safe(company) if company else 'PingWatch'
    return (
        f'<tr><td style="background:#f8f8f8;padding:14px 24px;'
        f'border-top:1px solid #e8e8e8">'
        f'<span style="font-size:11px;color:#aaa">'
        f'{_co} &nbsp;&middot;&nbsp; Alert Engine</span></td></tr>'
    )


def _build_alert_html(rows: list, event_type: str, severity: str,
                      title_device: str, title_sensor: str,
                      logo: bool = True, company: str = 'PingWatch',
                      ctx: dict = None) -> str:
    """Render a professional HTML email body.

    When ctx is provided, renders the full enriched layout with stats grid,
    breadcrumb path, and highlighted detail message. When ctx is None, falls
    back to simple key-value rows (used by test_smtp).
    """
    color, emoji, label = _status_style(event_type, severity)
    _co = _safe(company) if company else 'PingWatch'
    ts_str = _fmt_ts(ctx.get('ts', '')) if ctx else ''

    logo_s      = _html_logo_section(logo, _co)
    banner_s    = _html_status_banner(color, emoji, label, ts_str)
    breadcrumb_s = _html_breadcrumb(ctx) if ctx else ''
    detail_val  = _safe(ctx.get('detail', '')) if ctx else ''
    detail_s    = _html_detail_box(detail_val, color, severity)
    stats_s     = _html_stats_grid(ctx) if ctx else _html_legacy_rows(rows)
    footer_s    = _html_footer(_co)

    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f0f0f0;font-family:Arial,Helvetica,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f0f0;padding:32px 0">
<tr><td align="center">
<table width="580" cellpadding="0" cellspacing="0"
       style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.12)">
  {logo_s}
  {banner_s}
  {breadcrumb_s}
  {detail_s}
  {stats_s}
  {footer_s}
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
    _logo    = str(_cfg('email_logo', '1')) == '1'
    _company = _cfg('email_company_name', '') or 'PingWatch'

    if subject_tpl:
        subject = _fmt(subject_tpl)
    else:
        subject = f"[{_safe(_company)}] {emoji} {severity.upper()} \u2014 {dname}/{sname}"

    if body_tpl:
        body = _fmt(body_tpl)
        html = None
    else:
        # Plain-text fallback body
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
        if ctx.get('interval'):
            rows.append(('Interval', f"{ctx['interval']}s"))
        ms = ctx.get('ms')
        if ms is not None:
            rows.append(('Latency', f"{ms:.1f} ms"))
        if ctx.get('uptime_pct') is not None:
            rows.append(('Uptime', f"{ctx['uptime_pct']}%"))
        rows.append(('Detail', _safe(ctx.get('detail', ''))))
        body = '\n'.join(f"{lbl:<12}: {val}" for lbl, val in rows)
        html = _build_alert_html(rows, event_type, severity, dname, sname,
                                 logo=_logo, company=_company, ctx=ctx)

    recipients = [r.strip() for r in to_addrs.split(',') if r.strip()]
    _use_logo = html is not None and str(_cfg('email_logo', '1')) == '1'
    srv = None
    try:
        srv = _connect(host, port, tls, user, password)
        for rcpt in recipients:
            srv.sendmail(from_addr, [rcpt],
                         _build_msg(subject, body, from_addr, rcpt, html, logo=_use_logo).as_string())
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
        _now_iso = datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")
        mock_ctx = {
            'dname': 'Example Server', 'sname': 'ICMP Ping',
            'host': '192.168.1.1', 'stype': 'ping', 'grp': 'Production',
            'ms': 12.4, 'loss_pct': 0,
            'detail': 'This is a test email. SMTP connection is working correctly.',
            'interval': 60, 'warn_ms': 100, 'crit_ms': 200,
            'loss_warn_pct': 10, 'loss_crit_pct': 50,
            'total': 1440, 'success': 1438, 'uptime_pct': 99.9,
            'avg_ms': 14.2, 'min_ms': 8.1, 'max_ms': 45.3,
            'severity': 'info', 'event_type': 'info', 'ts': _now_iso,
        }
        rows = [('Status', 'SMTP test successful')]
        html = _build_alert_html(rows, 'info', 'info', 'SMTP Test', 'Connection OK',
                                 logo=_logo, company=_company, ctx=mock_ctx)
        srv.sendmail(from_addr, [to_addr],
                     _build_msg(subject, body, from_addr, to_addr, html, logo=_logo).as_string())
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
