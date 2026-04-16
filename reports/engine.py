"""
reports/engine.py — Render a report context to HTML / PDF via Jinja2 + WeasyPrint.

Public entrypoints:
  render_html(kind, context)  → str        (for in-browser preview)
  render_pdf(kind, context)   → bytes      (for download + email attachment)
"""

import datetime
import os
import time

from core.logger import log
from reports import charts

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Cached Jinja2 environment — lazy init so module imports cheaply
_env = None


def _get_env():
    global _env
    if _env is not None:
        return _env
    try:
        import jinja2
    except ImportError as e:
        raise RuntimeError("Jinja2 not installed — pip install Jinja2") from e

    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(_TEMPLATES_DIR),
        autoescape=jinja2.select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["datefmt"]    = _filter_datefmt
    env.filters["durfmt"]     = _filter_durfmt
    env.filters["msfmt"]      = _filter_msfmt
    env.filters["pctfmt"]     = _filter_pctfmt
    env.filters["statuspct"]  = _filter_statuspct
    env.filters["severity_class"] = _filter_severity
    env.filters["deltafmt"]   = _filter_deltafmt
    env.filters["trapname"]   = _filter_trapname
    env.filters["durfmt_flap"] = _filter_durfmt_flap
    _env = env
    return env


def _filter_durfmt_flap(duration, ongoing=False):
    """Render an incident duration honestly.

    Unlike the plain durfmt, this one doesn't pretend 0 means "no time" —
    which was confusing ("Duration: 0s" on a row that's actually still open
    or resolved sub-second). Pass a row dict's .ongoing via the 2-arg form:
      {{ row.duration_s | durfmt_flap(row.ongoing) }}
    """
    if ongoing:
        return "open"
    try:
        d = float(duration) if duration is not None else None
    except Exception:
        return "—"
    if d is None:
        return "open"
    if d <= 0:
        return "<1s"
    if d < 1:
        return "<1s"
    return _filter_durfmt(int(d))


# Vendor prefixes commonly used in MIB trap symbols — split off and upper-cased
# in the pretty label. Order matters (longest-first).
_TRAP_VENDOR_PREFIXES = [
    "cisco", "jnx", "fortinet", "fg", "fn", "apc", "hp", "juni", "dell", "ibm",
]

# Acronyms that should be ALL-CAPS in human-readable form. Anything not listed
# here stays Title-Cased (CamelCase split → words).
_TRAP_ACRONYMS = {
    "cpu": "CPU", "vpn": "VPN", "ssl": "SSL", "tls": "TLS", "dns": "DNS",
    "ip": "IP",   "tcp": "TCP", "udp": "UDP", "http": "HTTP", "https": "HTTPS",
    "ssh": "SSH", "bgp": "BGP", "ospf": "OSPF", "mpls": "MPLS", "vlan": "VLAN",
    "lan": "LAN", "wan": "WAN", "ha": "HA",  "qos": "QoS", "ups": "UPS",
    "nat": "NAT", "ids": "IDS", "ips": "IPS", "ntp": "NTP", "snmp": "SNMP",
    "faz": "FAZ", "fmg": "FMG", "fgt": "FGT", "api": "API", "psu": "PSU",
    "url": "URL", "mac": "MAC", "id":  "ID",
}


def _filter_trapname(raw):
    """Humanise a MIB trap symbol for display.

    Examples:
      fgTrapPerCpuHigh      → FG · Per CPU High
      fgTrapSslVpnLogin     → FG · SSL VPN Login
      fnTrapPowerSupply     → FN · Power Supply
      fgTrapVpnTunUp        → FG · VPN Tun Up
      coldStart             → Cold Start
      1.3.6.1.2.1.47.2.0.1  → 1.3.6.1.2.1.47.2.0.1  (unchanged — looks like OID)

    Non-destructive: the DB still stores the canonical MIB name. We only reshape
    for presentation so reports read naturally.
    """
    import re as _re
    s = str(raw or "").strip()
    if not s:
        return ""
    # Looks like an OID (dots and digits) — leave it alone
    if _re.fullmatch(r"[0-9.]+", s):
        return s

    vendor_tag = ""
    body = s

    # Peel off a lowercase vendor prefix like "fg", "fn", "cisco"
    low = body.lower()
    for pfx in _TRAP_VENDOR_PREFIXES:
        if low.startswith(pfx) and len(body) > len(pfx) and body[len(pfx)].isupper():
            vendor_tag = pfx.upper()
            body = body[len(pfx):]
            break

    # Drop the redundant "Trap" segment right after the vendor prefix
    if body.startswith("Trap") and len(body) > 4 and body[4].isupper():
        body = body[4:]

    # Split CamelCase / acronym runs:  "PerCpuHigh" → "Per Cpu High"
    parts = _re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+", body)
    if not parts:
        parts = [body]

    # Apply acronym casing; otherwise Title-case the fragment
    nice = []
    for p in parts:
        lp = p.lower()
        nice.append(_TRAP_ACRONYMS.get(lp, p[:1].upper() + p[1:].lower()))

    pretty = " ".join(nice).strip()
    if vendor_tag and pretty:
        return f"{vendor_tag} · {pretty}"
    return pretty or s


# ── Filters ───────────────────────────────────────────────────────────

def _filter_datefmt(ts, fmt="%Y-%m-%d %H:%M"):
    if not ts:
        return "—"
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime(fmt)
    except Exception:
        return "—"


def _filter_durfmt(seconds):
    """Human-friendly duration: 45s, 12m, 3h 20m, 2d 4h."""
    if seconds is None:
        return "—"
    try:
        s = int(seconds)
    except Exception:
        return "—"
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        h, m = divmod(s, 3600)
        m = m // 60
        return f"{h}h {m:02d}m"
    d, r = divmod(s, 86400)
    h = r // 3600
    return f"{d}d {h}h"


def _filter_msfmt(v):
    if v is None:
        return "—"
    try:
        return f"{float(v):.1f} ms"
    except Exception:
        return "—"


def _filter_pctfmt(v, places=2):
    if v is None:
        return "—"
    try:
        return f"{float(v):.{places}f}%"
    except Exception:
        return "—"


def _filter_statuspct(v):
    """Map an availability % to a status class: up / warn / down."""
    if v is None:
        return "muted"
    try:
        p = float(v)
    except Exception:
        return "muted"
    if p >= 99.9: return "up"
    if p >= 99.0: return "warn"
    return "down"


def _filter_deltafmt(v, unit="", decimals=2, good="lower"):
    """Render a delta value with arrow + sign. `good` is 'lower' or 'higher' —
    governs color: 'lower' means a negative delta is good (e.g. incidents),
    'higher' means a positive delta is good (e.g. uptime).
    Returns Markup-safe HTML (span with color class).
    """
    if v is None:
        return ""
    try:
        val = float(v)
    except Exception:
        return ""
    if abs(val) < 1e-9:
        return f'<span class="muted">no change</span>'
    up = val > 0
    positive_is_good = (good == "higher")
    is_good = (up and positive_is_good) or (not up and not positive_is_good)
    cls = "up" if is_good else "crit"
    arrow = "↑" if up else "↓"
    if decimals == 0:
        txt = f"{int(abs(val))}"
    else:
        txt = f"{abs(val):.{decimals}f}"
    return f'<span class="{cls}">{arrow} {txt}{unit}</span>'


def _filter_severity(direction):
    d = (direction or "").lower()
    if d in ("down", "threshold_crit"): return "crit"
    if d in ("threshold_warn", "anomaly_warn"): return "warn"
    if d in ("recovered", "threshold_ok"): return "ok"
    return "muted"


# ── Chart injection ───────────────────────────────────────────────────

def _attach_charts(ctx: dict) -> dict:
    """Generate chart data URIs and attach them under ctx['charts']."""
    try:
        from db.samples import db_load_availability
        avail_hourly = db_load_availability(
            max(60, int((ctx["period"]["end_ts"] - ctx["period"]["start_ts"]) / 60))
        )
    except Exception as e:
        log.debug(f"reports.engine availability chart data fetch failed: {e}")
        avail_hourly = []

    c = {}
    c["availability_trend"] = charts.availability_trend(avail_hourly)
    c["severity_donut"]     = charts.severity_donut(ctx["incidents"]["severity"])
    c["incident_timeline"]  = charts.incident_timeline(
        ctx["incidents"]["flaps"],
        ctx["period"]["start_ts"], ctx["period"]["end_ts"]
    )
    worst_rows = ctx["incidents"]["worst_5"]
    noisy_rows = ctx["incidents"]["noisy_5"]
    c["top_worst_bar"]      = charts.top_bar(
        [{"name": r["dname"], "fails": r["fail"]} for r in worst_rows],
        "fails", "name",
        title=f"Worst {len(worst_rows)} device{'s' if len(worst_rows) != 1 else ''} (failures)",
        color="#cf222e"
    )
    c["top_noisy_bar"]      = charts.top_bar(
        [{"name": f"{r['dname']}·{r['sname']}", "count": r["count"]}
         for r in noisy_rows],
        "count", "name",
        title=f"Top {len(noisy_rows)} noisiest sensor{'s' if len(noisy_rows) != 1 else ''} (incidents)",
        color="#9a6700"
    )
    if "latency" in ctx:
        c["latency_bar"] = charts.latency_percentile_bar(ctx["latency"], 10)
    ctx["charts"] = c
    return ctx


# ── Public ────────────────────────────────────────────────────────────

def _read_css(company_name: str = "") -> str:
    """Return the report stylesheet contents with per-render substitutions applied.

    The CSS uses __COMPANY_NAME__ as a placeholder in the @page footer string so
    the footer reads '<Org> Report' rather than a hard-coded product name.
    """
    global _CSS_CACHE
    try:
        cached = _CSS_CACHE
    except NameError:
        cached = None
    if cached is None:
        path = os.path.join(_TEMPLATES_DIR, "report.css")
        try:
            with open(path, "r", encoding="utf-8") as f:
                cached = f.read()
        except Exception:
            cached = ""
        globals()["_CSS_CACHE"] = cached
    name = (company_name or "PingWatch").strip() or "PingWatch"
    return cached.replace("__COMPANY_NAME__", name)


def render_html(kind: str, context: dict, embed_charts: bool = True,
                inline_css: bool = True) -> str:
    """Render the report to a full HTML document (for preview or PDF input).

    When `inline_css` is True, the print stylesheet is injected into a <style>
    block so the browser preview matches the PDF. For PDF rendering we pass
    `inline_css=False` and hand the CSS to WeasyPrint via stylesheets=[...],
    which handles @page rules correctly.
    """
    ctx = dict(context)
    if embed_charts:
        ctx = _attach_charts(ctx)
    ctx.setdefault("charts", {})   # templates reference charts.X unconditionally

    env = _get_env()
    tpl_name = f"{kind}.html"
    try:
        template = env.get_template(tpl_name)
    except Exception:
        log.warning(f"reports.engine: template {tpl_name!r} not found, falling back to executive.html")
        template = env.get_template("executive.html")
    html = template.render(**ctx)

    if inline_css:
        css = _read_css((ctx.get("company") or {}).get("name", ""))
        # Constrain the cover page for browser preview — @page rules only fire in print.
        preview_css = (
            "\n/* preview-only overrides */\n"
            ".cover{height:auto !important;min-height:60vh;padding:40mm 20mm !important;}\n"
            ".cover-logo{max-width:120px !important;max-height:120px !important;}\n"
            "body{padding:0 24px 40px 24px;max-width:900px;margin:0 auto;}\n"
        )
        style_block = f"<style>\n{css}\n{preview_css}\n</style>"
        if "</head>" in html:
            html = html.replace("</head>", style_block + "\n</head>", 1)
        else:
            html = style_block + html
    return html


_VALID_PDFA_VARIANTS = {"", "pdf/a-1b", "pdf/a-2b", "pdf/a-3b"}


def render_pdf(kind: str, context: dict, pdfa_mode: str = "") -> bytes:
    """Render the report to PDF bytes via WeasyPrint.

    If `pdfa_mode` is one of 'pdf/a-1b' / 'pdf/a-2b' / 'pdf/a-3b', the output
    claims the corresponding PDF/A conformance level. Needed by customers
    under document-retention mandates (finance, gov, ISO 27001 auditors).
    Requires WeasyPrint >= 62.

    When the WeasyPrint version is too old or the variant render fails for
    any reason, we fall back to a normal render and log a warning so the
    report still goes out — better a regular PDF than nothing.
    """
    try:
        from weasyprint import HTML, CSS
    except ImportError as e:
        raise RuntimeError(
            "WeasyPrint not installed. Install: pip install weasyprint "
            "(Linux also needs: apt install libpango-1.0-0 libpangoft2-1.0-0)"
        ) from e

    mode = (pdfa_mode or "").strip().lower()
    if mode not in _VALID_PDFA_VARIANTS:
        log.warning(f"reports.engine: ignoring unknown pdfa_mode {pdfa_mode!r}")
        mode = ""

    t0 = time.time()
    html_str = render_html(kind, context, embed_charts=True, inline_css=False)
    company_name = (context.get("company") or {}).get("name", "")
    css_str = _read_css(company_name)
    stylesheets = [CSS(string=css_str)] if css_str else None

    doc = HTML(string=html_str, base_url=_TEMPLATES_DIR)

    pdf_bytes = None
    if mode:
        try:
            pdf_bytes = doc.write_pdf(stylesheets=stylesheets, pdf_variant=mode)
        except TypeError:
            # WeasyPrint < 62 doesn't know the kwarg — downgrade silently
            log.warning(
                f"reports.engine: WeasyPrint too old for pdf_variant={mode!r}; "
                "falling back to standard PDF"
            )
        except Exception as e:
            log.warning(f"reports.engine: PDF/A render failed ({mode}): {e}; "
                        "falling back to standard PDF")

    if pdf_bytes is None:
        pdf_bytes = doc.write_pdf(stylesheets=stylesheets)
        mode_label = ""
    else:
        mode_label = f" [{mode}]"

    dt = int((time.time() - t0) * 1000)
    log.info(f"reports.engine: rendered {kind} PDF ({len(pdf_bytes)} bytes, {dt} ms){mode_label}")
    return pdf_bytes
