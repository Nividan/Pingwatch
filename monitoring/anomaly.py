"""Anomaly detection — per-sensor learned-baseline latency model.

Opt-in per sensor. Runs alongside (not in place of) static thresholds. Can only
promote a static "ok" verdict to "warn" — never fires "crit" on its own, so the
static-threshold ladder remains the ground truth for critical alerts.

Design is deliberately simple and hot-path safe:

    * EWMA mean + variance (Welford-style update) — O(1) CPU, 3 floats per sensor.
    * Adaptive learning rate: fast during bootstrap, slow once stable.
    * Variance floor prevents ultra-stable links from firing on tiny blips.
    * Upper-tail only (we do not alert on latency dropping).
    * 3-sample debounce before flipping to "warn".
    * Cold-start suppression: min_samples + a configurable time window.
    * No I/O. Caller (core.state) owns persistence via the _anom_dirty flag.

The ONLY side effect of this module is mutation of the sensor's `_anom_*`
attributes. It raises no exceptions (all paths return "ok" on internal error
after logging to the sensor logger).
"""

from __future__ import annotations
import math
import time

from core.logger import log_sensors
import core.settings as _settings

# Sensitivity dropdown → z-score threshold.
# 1 = strict  (more alerts, fewer misses)
# 2 = balanced (default)
# 3 = relaxed (fewer alerts, quieter)
_K_BY_SENSITIVITY = {1: 3.0, 2: 4.0, 3: 6.0}

# Sensor types that produce meaningful ms latency for baselining.
SUPPORTED_STYPES = frozenset({
    "ping", "tcp", "http", "dns", "http_keyword", "banner",
})


def _alpha(count: int) -> float:
    """Adaptive learning rate: fast bootstrap, slow steady-state."""
    if count < 50:
        return 0.10
    if count < 500:
        return 0.02
    return 0.01


def evaluate_anomaly(sensor, current_ms: float) -> str:
    """Update the sensor's EWMA baseline and return a threshold verdict.

    Returns "ok" or "warn" — never "crit".

    Caller contract:
      * sensor.anomaly_enabled is truthy
      * probe succeeded (ok=True, current_ms is a valid float)
      * static threshold evaluation returned "ok" (anomaly never overrides static)
      * sensor.stype is in SUPPORTED_STYPES (enforced by caller / UI)
    """
    try:
        # First probe ever for this sensor after enable: stamp enabled_since.
        if sensor._anom_enabled_since is None:
            sensor._anom_enabled_since = time.time()

        # Cold-start: initialize from first sample, do not alert.
        if sensor._anom_mean is None:
            sensor._anom_mean = float(current_ms)
            sensor._anom_var = 0.0
            sensor._anom_count = 1
            sensor._anom_dirty = True
            sensor._anom_consec_fails = 0
            return "ok"

        α = _alpha(sensor._anom_count)
        delta = float(current_ms) - sensor._anom_mean
        sensor._anom_mean += α * delta
        sensor._anom_var = (1.0 - α) * (sensor._anom_var + α * delta * delta)
        sensor._anom_count += 1
        sensor._anom_dirty = True

        # Bootstrap guard — need enough samples.
        if sensor._anom_count < int(sensor.anomaly_min_samples or 50):
            sensor._anom_consec_fails = 0
            return "ok"

        # Cold-start time guard — don't alert for N hours after first enable.
        cold_hours = int(_settings.get("anomaly_cold_start_hours", 24))
        if cold_hours > 0 and (time.time() - sensor._anom_enabled_since) < (cold_hours * 3600):
            sensor._anom_consec_fails = 0
            return "ok"

        # Global kill switch.
        if not int(_settings.get("anomaly_global_enabled", 1)):
            sensor._anom_consec_fails = 0
            return "ok"

        # Upper-tail z-test with variance floor.
        sens = int(sensor.anomaly_sensitivity or 2)
        k = _K_BY_SENSITIVITY.get(sens, _K_BY_SENSITIVITY[2])
        σ = math.sqrt(max(0.0, sensor._anom_var))
        σ_eff = max(σ, 10.0, 0.2 * sensor._anom_mean)
        z = (float(current_ms) - sensor._anom_mean) / σ_eff

        if current_ms > sensor._anom_mean and z > k:
            sensor._anom_consec_fails += 1
            if sensor._anom_consec_fails >= 3:
                return "warn"
            return "ok"

        sensor._anom_consec_fails = 0
        return "ok"

    except Exception as exc:
        log_sensors.warning(f"anomaly.evaluate_anomaly error: {exc!r}")
        return "ok"


def reset_baseline(sensor) -> None:
    """Wipe in-memory baseline state for a sensor. Caller must also delete
    the sensor_anomaly_baselines row if persisting."""
    sensor._anom_mean = None
    sensor._anom_var = None
    sensor._anom_count = 0
    sensor._anom_enabled_since = time.time() if sensor.anomaly_enabled else None
    sensor._anom_consec_fails = 0
    sensor._anom_state = "ok"
    sensor._anom_triggered_ts = None
    sensor._anom_dirty = True
