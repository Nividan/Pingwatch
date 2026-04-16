"""
reports/csv_export.py — Build a multi-section CSV "sidecar" from a report context.

Output layout (one big text blob, sections separated by a blank line + header comment):

    # Section: Availability by device
    did,dname,host,group,total,up,fail,pct
    ...

    # Section: Incidents (flaps)
    ts,device,sensor,type,direction,duration_s,detail
    ...

This lets managers pipe the same numbers the PDF shows into Excel / BI tools
without us having to maintain a separate endpoint per metric.
"""

import csv
import datetime
import io


def _section(writer, out, title):
    """Emit a comment header for a new section."""
    out.write(f"# Section: {title}\n")


def _iso(ts):
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


def build_csv_sidecar(ctx: dict) -> bytes:
    """
    Render the report context to a UTF-8 CSV byte-string.
    Sections included depend on ctx.kind.
    """
    buf = io.StringIO()
    # BOM — helps Excel open UTF-8 CSV correctly
    buf.write("\ufeff")

    # Header metadata
    meta   = ctx.get("meta",    {})
    period = ctx.get("period",  {})
    comp   = ctx.get("company", {})
    buf.write("# PingWatch report — CSV sidecar\n")
    buf.write(f"# Title: {meta.get('title','')}\n")
    buf.write(f"# Company: {comp.get('name','')}\n")
    buf.write(f"# Period: {period.get('label','')}  "
              f"({_iso(period.get('start_ts'))} -> {_iso(period.get('end_ts'))})\n")
    buf.write(f"# Generated: {_iso(meta.get('generated_at'))}"
              f"{' by ' + meta['generated_by'] if meta.get('generated_by') else ''}\n")
    if meta.get("severity_min") and meta["severity_min"] != "all":
        buf.write(f"# Severity filter: {meta['severity_min']}+\n")
    buf.write("\n")

    # ── Availability by device ─────────────────────────────────────
    avail = ctx.get("availability") or []
    if avail:
        _section(None, buf, "Availability by device")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["did", "dname", "host", "group", "total", "up", "fail", "pct"])
        for r in avail:
            w.writerow([r.get("did",""), r.get("dname",""), r.get("host",""),
                        r.get("group",""), r.get("total",0), r.get("up",0),
                        r.get("fail",0),
                        "" if r.get("pct") is None else r["pct"]])
        buf.write("\n")

    # ── Incident summary ──────────────────────────────────────────
    sev = (ctx.get("incidents") or {}).get("severity") or {}
    if sev:
        _section(None, buf, "Incident summary")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["metric", "value"])
        for k in ("total", "crit", "warn", "resolved"):
            w.writerow([k, sev.get(k, 0)])
        mttr = (ctx.get("incidents") or {}).get("mttr") or {}
        w.writerow(["mttr_s", "" if mttr.get("mttr_s") is None else mttr["mttr_s"]])
        w.writerow(["mtbf_s", "" if mttr.get("mtbf_s") is None else mttr["mtbf_s"]])
        buf.write("\n")

    # ── Top 5 worst / noisiest ────────────────────────────────────
    inc = ctx.get("incidents") or {}
    if inc.get("worst_5"):
        _section(None, buf, "Top worst-performing devices")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["did", "dname", "host", "pct", "total", "fail"])
        for r in inc["worst_5"]:
            w.writerow([r.get("did",""), r.get("dname",""), r.get("host",""),
                        "" if r.get("pct") is None else r["pct"],
                        r.get("total",0), r.get("fail",0)])
        buf.write("\n")
    if inc.get("noisy_5"):
        _section(None, buf, "Top noisiest sensors")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["did", "dname", "sid", "sname", "stype", "incidents"])
        for r in inc["noisy_5"]:
            w.writerow([r.get("did",""), r.get("dname",""), r.get("sid",""),
                        r.get("sname",""), r.get("stype",""), r.get("count",0)])
        buf.write("\n")

    # ── Latency percentiles ───────────────────────────────────────
    lat = ctx.get("latency") or []
    if lat:
        _section(None, buf, "Latency percentiles")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["did", "dname", "sid", "sname", "stype",
                    "samples", "p50_ms", "p95_ms", "p99_ms", "approx"])
        for r in lat:
            w.writerow([r.get("did",""), r.get("dname",""), r.get("sid",""),
                        r.get("sname",""), r.get("stype",""),
                        r.get("samples",0),
                        "" if r.get("p50") is None else r["p50"],
                        "" if r.get("p95") is None else r["p95"],
                        "" if r.get("p99") is None else r["p99"],
                        1 if r.get("approx") else 0])
        buf.write("\n")

    # ── SNMP trap counts ──────────────────────────────────────────
    traps = ctx.get("top_traps") or []
    if traps:
        _section(None, buf, "Top SNMP trap types")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["name", "vendor", "severity", "count"])
        for t in traps:
            w.writerow([t.get("name",""), t.get("vendor",""),
                        t.get("severity",""), t.get("count",0)])
        buf.write("\n")

    # ── TLS certs expiring ────────────────────────────────────────
    tls = ctx.get("tls_expiring") or []
    if tls:
        _section(None, buf, "TLS certificates expiring within 90 days")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["did", "dname", "sname", "host", "days_left", "bucket"])
        for c in tls:
            w.writerow([c.get("did",""), c.get("dname",""), c.get("sname",""),
                        c.get("host",""), c.get("days",""), c.get("bucket","")])
        buf.write("\n")

    # ── Incident list ─────────────────────────────────────────────
    flaps = (ctx.get("incidents") or {}).get("flaps") or []
    if flaps:
        _section(None, buf, "Incident log")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["ts", "did", "dname", "sid", "sname", "stype",
                    "direction", "duration_s", "resolved_at", "detail"])
        for f in flaps:
            w.writerow([_iso(f.get("ts")), f.get("did",""), f.get("dname",""),
                        f.get("sid",""),  f.get("sname",""), f.get("stype",""),
                        f.get("direction",""), f.get("duration",0),
                        _iso(f.get("resolved_at")) if f.get("resolved_at") else "",
                        (f.get("detail") or "")[:200]])
        buf.write("\n")

    # ── Inventory-kind extras ─────────────────────────────────────
    inv = ctx.get("inventory_devices") or []
    if inv:
        _section(None, buf, "Device inventory")
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(["did", "dname", "host", "group", "sensor_count", "up", "down"])
        for d in inv:
            w.writerow([d.get("did",""), d.get("dname",""), d.get("host",""),
                        d.get("group",""), d.get("sensor_count",0),
                        d.get("up",0), d.get("down",0)])
        buf.write("\n")

    return buf.getvalue().encode("utf-8")
