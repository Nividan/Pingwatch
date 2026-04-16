"""reports/ — PDF report generation module.

Public API:
  data.build_report_context(kind, period, filters)  → dict
  engine.render_pdf(template_kind, context)         → bytes
  engine.render_html_preview(template_kind, ctx)    → str
  scheduler.start_scheduler()                       → None
"""
