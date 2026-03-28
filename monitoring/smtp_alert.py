"""smtp_alert.py — stdlib SMTP email alerting for PingWatch."""
import smtplib
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from core.logger import log
from core.settings import get as _cfg

# Rate-limit repeated SMTP failures: suppress duplicate errors for 5 minutes
_last_error: dict = {}          # host -> (error_str, timestamp)
_ERROR_SUPPRESS_S = 300         # seconds between identical error logs


def _build_msg(subject, body, from_addr, to_addr):
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From']    = from_addr
    msg['To']      = to_addr
    msg.attach(MIMEText(body, 'plain'))
    return msg


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


def send_alert_email(direction, evt):
    """Send a flap alert email. Called in a daemon thread from state.py."""
    from db.backups import decrypt_pw as _dec_pw
    host      = _cfg('smtp_host', '')
    port      = _cfg('smtp_port', 587)
    tls       = _cfg('smtp_tls',  'starttls')  # 'ssl' | 'starttls' | 'none'
    user      = _cfg('smtp_user', '')
    password  = _dec_pw(_cfg('smtp_pass', ''))
    from_addr = _cfg('smtp_from', '')
    to_addr   = _cfg('smtp_to',   '')
    if not (host and from_addr and to_addr):
        return
    emoji = '\U0001f534' if direction == 'down' else '\U0001f7e2'
    label = 'DOWN' if direction == 'down' else 'RECOVERED'
    subject = f"[PingWatch] {emoji} {label}: {_safe(evt.get('dname'))} / {_safe(evt.get('sname'))}"
    body = (
        f"Status : {label}\n"
        f"Device : {_safe(evt.get('dname'))}\n"
        f"Sensor : {_safe(evt.get('sname'))} ({_safe(evt.get('stype'))})\n"
        f"Host   : {_safe(evt.get('host'))}\n"
        f"Time   : {_safe(evt.get('ts'))}\n"
        f"Detail : {_safe(evt.get('detail'))}\n"
    )
    srv = None
    try:
        srv = _connect(host, port, tls, user, password)
        srv.sendmail(from_addr, [to_addr], _build_msg(subject, body, from_addr, to_addr).as_string())
        srv.quit(); srv = None
        _last_error.pop(host, None)   # clear suppression on success
        log.info(f"Alert email sent ({label}): {evt.get('dname')}/{evt.get('sname')}")
    except Exception as e:
        err_str = str(e)
        now = time.monotonic()
        last_err, last_ts = _last_error.get(host, (None, 0))
        if err_str != last_err or (now - last_ts) >= _ERROR_SUPPRESS_S:
            log.error(f"SMTP alert failed (host={host}:{port}): {e}")
            _last_error[host] = (err_str, now)
        # else: suppress repeated identical error
    finally:
        if srv:
            try: srv.quit()
            except Exception: pass


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

    subject = _fmt(subject_tpl) if subject_tpl else (
        f"[PingWatch] {_safe(ctx.get('severity','').upper())} — "
        f"{_safe(ctx.get('dname'))}/{_safe(ctx.get('sname'))}"
    )
    if body_tpl:
        body = _fmt(body_tpl)
    else:
        body = (
            f"Alert Rule Triggered\n"
            f"{'─' * 40}\n"
            f"Event    : {_safe(ctx.get('event_type',''))}\n"
            f"Device   : {_safe(ctx.get('dname'))}\n"
            f"Sensor   : {_safe(ctx.get('sname'))} ({_safe(ctx.get('stype'))})\n"
            f"Host     : {_safe(ctx.get('host'))}\n"
            f"Severity : {_safe(ctx.get('severity',''))}\n"
            f"Time     : {_safe(ctx.get('ts',''))}\n"
            f"Detail   : {_safe(ctx.get('detail',''))}\n"
        )

    recipients = [r.strip() for r in to_addrs.split(',') if r.strip()]
    srv = None
    try:
        srv = _connect(host, port, tls, user, password)
        for rcpt in recipients:
            srv.sendmail(from_addr, [rcpt],
                         _build_msg(subject, body, from_addr, rcpt).as_string())
        srv.quit(); srv = None
        _last_error.pop(host, None)
        log.info(f"Rule alert email sent to {to_addrs}: {subject[:60]}")
    except Exception as e:
        err_str = str(e)
        now = time.monotonic()
        last_err, last_ts = _last_error.get(host, (None, 0))
        if err_str != last_err or (now - last_ts) >= _ERROR_SUPPRESS_S:
            log.error(f"Rule alert SMTP failed (host={host}:{port}): {e}")
            _last_error[host] = (err_str, now)
    finally:
        if srv:
            try: srv.quit()
            except Exception: pass


def test_smtp(cfg):
    """Test SMTP with provided config dict. Returns (ok:bool, msg:str)."""
    srv = None
    try:
        srv = _connect(
            cfg['host'], cfg.get('port', 587), cfg.get('tls', 'starttls'),
            cfg.get('user', ''), cfg.get('password', '')
        )
        from_addr = cfg.get('from_addr', 'pingwatch@test')
        to_addr   = cfg.get('to_addr', from_addr)
        subject   = '[PingWatch] SMTP test \u2014 connection OK'
        body      = 'This is a test email from PingWatch SMTP alert system.'
        srv.sendmail(from_addr, [to_addr], _build_msg(subject, body, from_addr, to_addr).as_string())
        srv.quit(); srv = None
        return True, 'Test email sent successfully.'
    except Exception as e:
        return False, str(e)
    finally:
        if srv:
            try: srv.quit()
            except Exception: pass
