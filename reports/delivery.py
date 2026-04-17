"""
reports/delivery.py — Email a generated report PDF to recipients.

Reuses SMTP config + connection pattern from monitoring/smtp_alert.py.
"""

import os
import smtplib
from email.mime.base      import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from email               import encoders

from core.logger   import log
from core.settings import get as _cfg


def _safe(v: str) -> str:
    return str(v or "").replace("\r", "").replace("\n", " ")


def _resolve_recipients(sch: dict) -> list:
    """Merge recipient_group (by id) + recipient_emails (explicit list) into one deduped list."""
    out = []
    seen = set()

    for e in sch.get("recipient_emails") or []:
        e = (e or "").strip()
        if e and e not in seen:
            out.append(e)
            seen.add(e)

    grp_id = sch.get("recipient_group") or 0
    if grp_id:
        try:
            from db.groups import db_resolve_group_emails
            group_emails = db_resolve_group_emails(int(grp_id)) or []
            for e in group_emails:
                e = (e or "").strip()
                if e and e not in seen:
                    out.append(e)
                    seen.add(e)
        except Exception as e:
            log.warning(f"reports.delivery group resolve failed: {e}")

    return out


def _render_subject_body(sch: dict, ctx: dict, pdf_bytes: int) -> tuple:
    """
    Render subject_tpl and body_tpl using a small placeholder set.
    Placeholders: {company}, {period}, {title}, {crit}, {warn}, {uptime}, {pdf_kb}.
    """
    company = ctx.get("company", {}).get("name", "PingWatch")
    period  = ctx.get("period",  {}).get("label", "")
    title   = ctx.get("meta",    {}).get("title", "Report")
    sev     = ctx.get("incidents", {}).get("severity", {}) or {}
    overall = ctx.get("overall",   {}).get("pct")
    tokens = {
        "company": _safe(company),
        "period":  _safe(period),
        "title":   _safe(title),
        "crit":    sev.get("crit", 0),
        "warn":    sev.get("warn", 0),
        "total":   sev.get("total", 0),
        "uptime":  f"{overall:.2f}%" if overall is not None else "—",
        "pdf_kb":  pdf_bytes // 1024 if pdf_bytes else 0,
    }

    subject_tpl = sch.get("subject_tpl") or "[{company}] {title} — {period}"
    body_tpl    = sch.get("body_tpl") or (
        "Monthly monitoring report attached.\n\n"
        "Company: {company}\n"
        "Period:  {period}\n"
        "Uptime:  {uptime}\n"
        "Crit:    {crit}\n"
        "Warn:    {warn}\n\n"
        "— PingWatch"
    )

    def _fmt(tpl):
        try:
            return tpl.format(**tokens)
        except (KeyError, ValueError):
            return tpl

    return _fmt(subject_tpl), _fmt(body_tpl)


def _smtp_connect():
    from db.backups import decrypt_pw as _dec_pw
    host = _cfg("smtp_host", "")
    port = _cfg("smtp_port", 587)
    tls  = _cfg("smtp_tls",  "starttls")
    user = _cfg("smtp_user", "")
    pwd  = _dec_pw(_cfg("smtp_pass", ""))
    if not host:
        raise RuntimeError("SMTP host not configured")
    if tls == "ssl":
        srv = smtplib.SMTP_SSL(host, int(port), timeout=15)
    else:
        srv = smtplib.SMTP(host, int(port), timeout=15)
        if tls == "starttls":
            srv.starttls()
    if user:
        srv.login(user, pwd)
    return srv, _cfg("smtp_from", "")


def send_report_email(recipients: list,
                      subject: str,
                      body: str,
                      pdf_bytes: bytes,
                      pdf_filename: str = "report.pdf",
                      csv_bytes: bytes = None,
                      csv_filename: str = None) -> tuple:
    """
    Send a report email with PDF attached (and optionally a CSV sidecar).
    Returns (ok: bool, error: str).
    """
    if not recipients:
        return False, "no recipients"

    try:
        msg = MIMEMultipart()
        msg["Subject"] = _safe(subject)
        msg["To"]      = ", ".join(_safe(r) for r in recipients)
        msg.attach(MIMEText(body, "plain"))

        # PDF attachment
        att = MIMEBase("application", "pdf")
        att.set_payload(pdf_bytes)
        encoders.encode_base64(att)
        att.add_header("Content-Disposition",
                       f'attachment; filename="{_safe(pdf_filename)}"')
        msg.attach(att)

        # Optional CSV sidecar
        if csv_bytes:
            cname = csv_filename or "report.csv"
            cs = MIMEBase("text", "csv")
            cs.set_payload(csv_bytes)
            encoders.encode_base64(cs)
            cs.add_header("Content-Disposition",
                          f'attachment; filename="{_safe(cname)}"')
            msg.attach(cs)

        srv, from_addr = _smtp_connect()
        try:
            msg["From"] = from_addr or "pingwatch@localhost"
            srv.sendmail(from_addr or "pingwatch@localhost", recipients, msg.as_string())
        finally:
            try: srv.quit()
            except Exception: pass

        log.info(f"reports.delivery sent to {len(recipients)} recipient(s), "
                 f"{len(pdf_bytes)} bytes"
                 + (f" + {len(csv_bytes)} CSV bytes" if csv_bytes else ""))
        return True, ""
    except Exception as e:
        log.error(f"reports.delivery send failed: {e}")
        return False, str(e)[:400]
