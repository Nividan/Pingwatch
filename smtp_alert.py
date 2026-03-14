"""smtp_alert.py — stdlib SMTP email alerting for PingWatch."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from logger import log
from settings import get as _cfg


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
    host      = _cfg('smtp_host', '')
    port      = _cfg('smtp_port', 587)
    tls       = _cfg('smtp_tls',  'starttls')  # 'ssl' | 'starttls' | 'none'
    user      = _cfg('smtp_user', '')
    password  = _cfg('smtp_pass', '')
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
        log.info(f"Alert email sent ({label}): {evt.get('dname')}/{evt.get('sname')}")
    except Exception as e:
        log.error(f"SMTP alert failed: {e}")
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
