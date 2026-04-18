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

# Probe status tracking — distinct from send status so the badge can show both
# "Last verified" (probe at startup or post-save) and "Last email sent"
# (real outbound delivery) as separate signals.
_last_probe_ok_ts: float = 0
_last_probe_err: dict = {'ts': 0.0, 'msg': ''}

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


def _days_status(days_left, state: str = '') -> tuple:
    """Map (days_left, state) to (status_label, days_row_label, days_value_str).

    Used by both license and TLS renderers since they share an expiry-based
    semantic. `state` is the threshold state ('ok' | 'warn' | 'crit').
    """
    try:
        d = int(days_left)
    except (TypeError, ValueError):
        return ('Unknown', 'Days', '\u2014')
    sv = (state or '').lower()
    if sv == 'ok':
        return ('Active', 'Days Remaining', f'{d} days')
    if d < 0:
        return ('EXPIRED', 'Days Since Expiry', f'{-d} days')
    if sv == 'crit':
        return ('Expires soon (critical)', 'Days Remaining', f'{d} days')
    if sv == 'warn':
        return ('Expires soon', 'Days Remaining', f'{d} days')
    return ('Valid', 'Days Remaining', f'{d} days')


def _render_latency_body(ctx: dict) -> str:
    """HTML rows for latency-first sensors (ping/tcp/http/dns/http_keyword/banner)."""
    html = ''
    ri = 0
    html += _html_section_hdr('Sensor Details')
    for lbl, val in [
        ('Host',     _safe(ctx.get('host', '')) or '\u2014'),
        ('Type',     _safe(ctx.get('stype', '')) or '\u2014'),
        ('Group',    _safe(ctx.get('grp', '')) or '\u2014'),
        ('Interval', f"{ctx['interval']}s" if ctx.get('interval') else '\u2014'),
    ]:
        html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1

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
    return html


def _render_license_body(ctx: dict) -> str:
    """HTML rows for license sensors — expiry-centric, no latency."""
    html = ''
    ri = 0
    html += _html_section_hdr('Sensor Details')
    for lbl, val in [
        ('Device',  _safe(ctx.get('dname', '')) or '\u2014'),
        ('License', _safe(ctx.get('sname', '')) or '\u2014'),
        ('Host',    _safe(ctx.get('host',  '')) or '\u2014'),
        ('Group',   _safe(ctx.get('grp',   '')) or '\u2014'),
    ]:
        html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1

    status, days_lbl, days_val = _days_status(ctx.get('days_left'), ctx.get('state'))
    html += _html_section_hdr('License Status')
    lic_rows = [
        ('Status',      status),
        ('Expiry Date', _safe(ctx.get('expiry_date', '')) or '\u2014'),
        (days_lbl,      days_val),
    ]
    dur = _fmt_duration(ctx.get('duration_s'))
    if dur:
        lic_rows.append(('Duration', dur))
    for lbl, val in lic_rows:
        html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1
    return html


def _render_tls_body(ctx: dict) -> str:
    """HTML rows for TLS certificate sensors — expiry + handshake latency."""
    html = ''
    ri = 0
    host = _safe(ctx.get('host', ''))
    port = ctx.get('port')
    endpoint = f"{host}:{port}" if (host and port) else (host or '\u2014')
    html += _html_section_hdr('Sensor Details')
    for lbl, val in [
        ('Device',   _safe(ctx.get('dname', '')) or '\u2014'),
        ('Endpoint', endpoint),
        ('Type',     'TLS Certificate'),
        ('Group',    _safe(ctx.get('grp', '')) or '\u2014'),
    ]:
        html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1

    # TLS ctx carries the remaining-days count in `last_value` (set by probe_tls).
    try:
        days_left = int(ctx.get('last_value') or 0)
    except (TypeError, ValueError):
        days_left = 0
    status, days_lbl, days_val = _days_status(days_left, ctx.get('state'))
    ms = ctx.get('ms')
    html += _html_section_hdr('Certificate')
    cert_rows = [
        ('Status',  status),
        (days_lbl,  days_val),
        ('Latency', f'{ms:.1f} ms' if ms is not None else '\u2014'),
    ]
    dur = _fmt_duration(ctx.get('duration_s'))
    if dur:
        cert_rows.append(('Duration', dur))
    for lbl, val in cert_rows:
        html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1

    # For TLS the warn_ms/crit_ms fields hold day-thresholds (misnomer in
    # the schema; sensor engine treats them as days).
    thr = []
    if ctx.get('warn_ms'):
        thr.append(('Warn', f"&lt; {ctx['warn_ms']} days"))
    if ctx.get('crit_ms'):
        thr.append(('Crit', f"&lt; {ctx['crit_ms']} days"))
    if thr:
        html += _html_section_hdr('Thresholds')
        for lbl, val in thr:
            html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1
    return html


def _render_snmp_body(ctx: dict) -> str:
    """HTML rows for SNMP sensors — value + OID, no loss/uptime."""
    html = ''
    ri = 0
    unit = _safe(ctx.get('snmp_unit', ''))
    oid  = _safe(ctx.get('snmp_oid', ''))
    rows = [
        ('Device', _safe(ctx.get('dname', '')) or '\u2014'),
        ('Sensor', _safe(ctx.get('sname', '')) or '\u2014'),
        ('Host',   _safe(ctx.get('host',  '')) or '\u2014'),
    ]
    if oid:
        rows.append(('OID', oid))
    if unit:
        rows.append(('Unit', unit))
    rows.append(('Group', _safe(ctx.get('grp', '')) or '\u2014'))
    html += _html_section_hdr('Sensor Details')
    for lbl, val in rows:
        html += _html_stat_row(lbl, val, ri % 2 == 0); ri += 1

    val = _safe(ctx.get('last_value', '')) or '\u2014'
    ms = ctx.get('ms')
    html += _html_section_hdr('Reading')
    read_rows = [
        ('Current Value', val),
        ('Latency',       f'{ms:.1f} ms' if ms is not None else '\u2014'),
    ]
    dur = _fmt_duration(ctx.get('duration_s'))
    if dur:
        read_rows.append(('Duration', dur))
    for lbl, value in read_rows:
        html += _html_stat_row(lbl, value, ri % 2 == 0); ri += 1

    # For SNMP warn_ms/crit_ms are value thresholds (field names are a misnomer).
    thr = []
    u_suffix = f' {unit}' if unit else ''
    if ctx.get('warn_ms'):
        thr.append(('Warn', f"&gt; {ctx['warn_ms']}{u_suffix}"))
    if ctx.get('crit_ms'):
        thr.append(('Crit', f"&gt; {ctx['crit_ms']}{u_suffix}"))
    if thr:
        html += _html_section_hdr('Thresholds')
        for lbl, value in thr:
            html += _html_stat_row(lbl, value, ri % 2 == 0); ri += 1
    return html


_STATS_RENDERERS = {
    'license': _render_license_body,
    'tls':     _render_tls_body,
    'snmp':    _render_snmp_body,
}


def _html_stats_grid(ctx: dict) -> str:
    """Render the monitoring stats grid. Dispatches to a type-specific renderer
    so value-first sensors (license/TLS/SNMP) don't get a misleading latency
    Performance section."""
    if not ctx:
        return ''
    renderer = _STATS_RENDERERS.get((ctx.get('stype') or '').lower(),
                                    _render_latency_body)
    inner = renderer(ctx)
    return (
        f'<tr><td style="padding:8px 24px 16px">'
        f'<table width="100%" cellpadding="0" cellspacing="0">'
        f'{inner}</table></td></tr>'
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


def _render_latency_text(ctx: dict) -> list:
    """Plain-text rows for latency-first sensors."""
    rows = [
        ('Event',    _safe(ctx.get('event_type', ''))),
        ('Device',   _safe(ctx.get('dname', ''))),
        ('Sensor',   f"{_safe(ctx.get('sname', ''))} ({_safe(ctx.get('stype', ''))})"),
        ('Host',     _safe(ctx.get('host', ''))),
        ('Severity', _safe(ctx.get('severity', ''))),
        ('Time',     _fmt_ts(ctx.get('ts', ''))),
    ]
    dur = _fmt_duration(ctx.get('duration_s'))
    if dur:
        rows.append(('Duration', dur))
    if ctx.get('interval'):
        rows.append(('Interval', f"{ctx['interval']}s"))
    ms = ctx.get('ms')
    if ms is not None:
        rows.append(('Latency', f"{ms:.1f} ms"))
    if ctx.get('uptime_pct') is not None:
        rows.append(('Uptime', f"{ctx['uptime_pct']}%"))
    rows.append(('Detail', _safe(ctx.get('detail', ''))))
    return rows


def _render_license_text(ctx: dict) -> list:
    status, days_lbl, days_val = _days_status(ctx.get('days_left'), ctx.get('state'))
    rows = [
        ('Event',       _safe(ctx.get('event_type', ''))),
        ('Device',      _safe(ctx.get('dname', ''))),
        ('License',     _safe(ctx.get('sname', ''))),
        ('Host',        _safe(ctx.get('host', ''))),
        ('Severity',    _safe(ctx.get('severity', ''))),
        ('Time',        _fmt_ts(ctx.get('ts', ''))),
        ('Status',      status),
        ('Expiry Date', _safe(ctx.get('expiry_date', ''))),
        (days_lbl,      days_val),
    ]
    dur = _fmt_duration(ctx.get('duration_s'))
    if dur:
        rows.append(('Duration', dur))
    rows.append(('Detail', _safe(ctx.get('detail', ''))))
    return rows


def _render_tls_text(ctx: dict) -> list:
    try:
        days_left = int(ctx.get('last_value') or 0)
    except (TypeError, ValueError):
        days_left = 0
    status, days_lbl, days_val = _days_status(days_left, ctx.get('state'))
    host = _safe(ctx.get('host', ''))
    port = ctx.get('port')
    endpoint = f"{host}:{port}" if (host and port) else host
    rows = [
        ('Event',    _safe(ctx.get('event_type', ''))),
        ('Device',   _safe(ctx.get('dname', ''))),
        ('Endpoint', endpoint),
        ('Severity', _safe(ctx.get('severity', ''))),
        ('Time',     _fmt_ts(ctx.get('ts', ''))),
        ('Status',   status),
        (days_lbl,   days_val),
    ]
    ms = ctx.get('ms')
    if ms is not None:
        rows.append(('Latency', f"{ms:.1f} ms"))
    dur = _fmt_duration(ctx.get('duration_s'))
    if dur:
        rows.append(('Duration', dur))
    rows.append(('Detail', _safe(ctx.get('detail', ''))))
    return rows


def _render_snmp_text(ctx: dict) -> list:
    unit = _safe(ctx.get('snmp_unit', ''))
    rows = [
        ('Event',     _safe(ctx.get('event_type', ''))),
        ('Device',    _safe(ctx.get('dname', ''))),
        ('Sensor',    _safe(ctx.get('sname', ''))),
        ('Host',      _safe(ctx.get('host', ''))),
        ('Severity',  _safe(ctx.get('severity', ''))),
        ('Time',      _fmt_ts(ctx.get('ts', ''))),
    ]
    oid = _safe(ctx.get('snmp_oid', ''))
    if oid:
        rows.append(('OID', oid))
    if unit:
        rows.append(('Unit', unit))
    val = _safe(ctx.get('last_value', ''))
    if val:
        rows.append(('Current Value', val))
    ms = ctx.get('ms')
    if ms is not None:
        rows.append(('Latency', f"{ms:.1f} ms"))
    dur = _fmt_duration(ctx.get('duration_s'))
    if dur:
        rows.append(('Duration', dur))
    rows.append(('Detail', _safe(ctx.get('detail', ''))))
    return rows


_TEXT_RENDERERS = {
    'license': _render_license_text,
    'tls':     _render_tls_text,
    'snmp':    _render_snmp_text,
}


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


def _smtp_probe(host, port, tls, user, password) -> None:
    """Verify SMTP reachability without sending an email.

    Reuses _connect() so the probe exercises the exact code path a real send
    would take (TCP → optional STARTTLS → optional AUTH). Closes the session
    immediately with QUIT — never calls sendmail. Raises on any failure so the
    caller can record the error.
    """
    srv = _connect(host, port, tls, user, password)
    try:
        srv.quit()
    except Exception:
        # QUIT failure is benign — we already proved auth + handshake worked.
        # Don't let a flaky server's broken close-handshake mark the probe failed.
        try:
            srv.close()
        except Exception:
            pass


def run_smtp_startup_probe() -> tuple:
    """Probe SMTP connectivity and persist the result.

    Returns (ok: bool, msg: str). Never raises.

    Skipped quietly (DEBUG log) when SMTP is not configured. On any other
    outcome, updates the in-memory probe state vars and persists three keys
    to app_settings so the badge survives restarts:
      smtp_last_check_ts    — unix timestamp (float)
      smtp_last_check_ok    — '1' / '0'
      smtp_last_check_error — human-readable error or empty
    """
    global _last_probe_ok_ts, _last_probe_err
    from db.backups import decrypt_pw as _dec_pw
    host     = str(_cfg('smtp_host', '')).strip()
    port     = _cfg('smtp_port', 587)
    tls      = _cfg('smtp_tls',  'starttls')
    user     = _cfg('smtp_user', '')
    password = _dec_pw(_cfg('smtp_pass', ''))

    if not host:
        log.debug("SMTP startup probe: skipped (not configured)")
        return False, 'not configured'

    now = time.time()
    try:
        _smtp_probe(host, port, tls, user, password)
        _last_probe_ok_ts = now
        _last_probe_err   = {'ts': 0.0, 'msg': ''}
        log.info(f"SMTP startup probe: OK ({host}:{port})")
        _persist_probe_result(now, True, '')
        return True, 'ok'
    except Exception as e:
        msg = str(e)[:200]
        _last_probe_err = {'ts': now, 'msg': msg}
        log.warning(f"SMTP startup probe failed ({host}:{port}): {e}")
        _persist_probe_result(now, False, msg)
        return False, msg


def _persist_probe_result(ts: float, ok: bool, err_msg: str) -> None:
    """Persist probe result to app_settings so the badge survives restarts."""
    try:
        from core.settings import load as _sl
        from db import _db_enqueue, db_save_settings
        data = {
            'smtp_last_check_ts':    f'{ts:.0f}',
            'smtp_last_check_ok':    '1' if ok else '0',
            'smtp_last_check_error': err_msg,
        }
        _sl(data)
        _db_enqueue(lambda d=data: db_save_settings(d))
    except Exception as e:
        log.debug(f"SMTP probe: could not persist result: {e}")


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
    _company = _cfg('org_name', '') or 'PingWatch'

    if subject_tpl:
        subject = _fmt(subject_tpl)
    else:
        subject = f"[{_safe(_company)}] {emoji} {severity.upper()} \u2014 {dname}/{sname}"

    if body_tpl:
        body = _fmt(body_tpl)
        html = None
    else:
        # Plain-text fallback body — dispatched by stype so license/tls/snmp
        # don't emit the latency-shaped fields.
        _text_renderer = _TEXT_RENDERERS.get((ctx.get('stype') or '').lower(),
                                             _render_latency_text)
        rows = _text_renderer(ctx)
        body = '\n'.join(f"{lbl:<16}: {val}" for lbl, val in rows)
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
        _company = _cfg('org_name', '') or 'PingWatch'
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


def _restore_probe_state_from_settings() -> None:
    """Hydrate the in-memory probe state from app_settings on first read.

    Without this, get_smtp_status() returns stale 'configured' (yellow) until
    the next probe fires — even though the previous probe succeeded and was
    persisted. Called lazily from get_smtp_status() so module import stays
    free of DB access.
    """
    global _last_probe_ok_ts, _last_probe_err
    if _last_probe_ok_ts or _last_probe_err['ts']:
        return  # already hydrated this session
    try:
        ts_raw = str(_cfg('smtp_last_check_ts', '') or '').strip()
        if not ts_raw:
            return
        ts = float(ts_raw)
        ok = str(_cfg('smtp_last_check_ok', '0')) == '1'
        if ok:
            _last_probe_ok_ts = ts
        else:
            _last_probe_err = {
                'ts':  ts,
                'msg': str(_cfg('smtp_last_check_error', '') or '')[:200],
            }
    except Exception:
        pass


def get_smtp_status() -> dict:
    """Return connection status dict for the Settings API.

    State combines two signals:
      - send: last real outbound delivery (_last_ok_ts / _last_err)
      - probe: startup / post-save connectivity check (_last_probe_*)

    A successful probe is sufficient to flip the badge green even when no
    real email has been sent yet — that's the whole point of the probe.
    """
    _restore_probe_state_from_settings()
    host = str(_cfg('smtp_host', '')).strip()
    # Use whichever success signal is newer
    ok_ts = max(_last_ok_ts or 0, _last_probe_ok_ts or 0) or 0
    # Most-recent error wins, but only if newer than the most-recent success
    err_ts  = max(_last_err['ts'] or 0, _last_probe_err['ts'] or 0) or 0
    err_msg = (
        _last_probe_err['msg']
        if (_last_probe_err['ts'] or 0) >= (_last_err['ts'] or 0)
        else _last_err['msg']
    )

    if not host:
        state = 'unconfigured'
    elif err_ts and err_ts > ok_ts:
        state = 'error'
    elif ok_ts:
        state = 'ok'
    else:
        state = 'configured'   # host is set but no probe has fired yet

    return {
        'state':            state,
        'last_ok_ts':       _last_ok_ts or None,
        'last_err_ts':      _last_err['ts'] or None,
        'last_err_msg':     _last_err['msg'],
        'last_probe_ok_ts': _last_probe_ok_ts or None,
        'last_probe_err_ts':  _last_probe_err['ts'] or None,
        'last_probe_err_msg': _last_probe_err['msg'],
    }
