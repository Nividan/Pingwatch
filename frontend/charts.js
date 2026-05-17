// ───────────────────────────────────────────────────────────────
// PingWatch — Vanilla SVG chart helpers
// Ported from the design prototype (shell.jsx). No deps.
//   sparkline(data, opts)   → SVG string (line w/ optional gradient fill)
//   donut(segments, opts)   → SVG string (status breakdown donut)
//   heatmap(grid, opts)     → SVG string (X×Y latency-style heatmap)
// All return strings — caller does el.innerHTML = ...
// ───────────────────────────────────────────────────────────────
(function (global) {
  'use strict';

  /**
   * Tiny sparkline.
   * @param {number[]} data   Y-values; X is implicit (evenly spaced).
   * @param {object}  [opts]
   *   w (110), h (24)         pixel size
   *   color ('var(--accent)') stroke + gradient color
   *   fill (true)             draw soft gradient under the line
   *   stroke (1.2)            line width
   * @returns {string} SVG
   */
  function sparkline(data, opts) {
    opts = opts || {};
    var w = opts.w || 110, h = opts.h || 24;
    var color = opts.color || 'var(--accent)';
    var fill = opts.fill !== false;
    var stroke = opts.stroke || 1.2;

    if (!data || !data.length) {
      return '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '"></svg>';
    }
    var lo = Math.min.apply(null, data);
    var hi = Math.max.apply(null, data);
    var range = hi - lo || 1;
    var step = data.length > 1 ? w / (data.length - 1) : 0;
    var pts = [];
    for (var i = 0; i < data.length; i++) {
      var x = i * step;
      var y = h - 2 - ((data[i] - lo) / range) * (h - 4);
      pts.push([x, y]);
    }
    var line = '';
    for (var j = 0; j < pts.length; j++) {
      line += (j === 0 ? 'M' : 'L') + pts[j][0].toFixed(1) + ' ' + pts[j][1].toFixed(1);
      if (j < pts.length - 1) line += ' ';
    }
    var area = line + ' L ' + w + ' ' + h + ' L 0 ' + h + ' Z';
    var gid = 'pw-sg-' + Math.abs(_strHash(color));

    var out = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '" preserveAspectRatio="none" style="display:block">';
    if (fill) {
      out += '<defs><linearGradient id="' + gid + '" x1="0" y1="0" x2="0" y2="1">'
           +   '<stop offset="0" stop-color="' + color + '" stop-opacity=".30"/>'
           +   '<stop offset="1" stop-color="' + color + '" stop-opacity="0"/>'
           + '</linearGradient></defs>';
      out += '<path d="' + area + '" fill="url(#' + gid + ')"/>';
    }
    out += '<path d="' + line + '" fill="none" stroke="' + color
        +  '" stroke-width="' + stroke + '" stroke-linejoin="round" stroke-linecap="round"/>';
    out += '</svg>';
    return out;
  }

  /**
   * Status-breakdown donut.
   * @param {Array<{value:number,color:string,label?:string}>} segments
   * @param {object} [opts]
   *   size (110), stroke (14)
   *   centerLabel (true)      show big % + label in middle
   *   centerLabelText ('HEALTHY')  label under the percentage
   * @returns {string} SVG
   */
  function donut(segments, opts) {
    opts = opts || {};
    var size = opts.size || 110;
    var stroke = opts.stroke || 14;
    var showCenter = opts.centerLabel !== false;
    var labelText = opts.centerLabelText || 'HEALTHY';
    var total = 0;
    for (var i = 0; i < segments.length; i++) total += segments[i].value;
    if (total === 0) total = 1;
    var r = (size - stroke) / 2;
    var c = 2 * Math.PI * r;
    var offset = 0;
    var out = '<svg width="' + size + '" height="' + size + '" viewBox="0 0 ' + size + ' ' + size + '">';
    out += '<circle cx="' + (size/2) + '" cy="' + (size/2) + '" r="' + r
        +  '" fill="none" stroke="var(--bg3)" stroke-width="' + stroke + '"/>';
    for (var k = 0; k < segments.length; k++) {
      var s = segments[k];
      var len = (s.value / total) * c;
      out += '<circle cx="' + (size/2) + '" cy="' + (size/2) + '" r="' + r + '"'
          +  ' fill="none" stroke="' + s.color + '" stroke-width="' + stroke + '"'
          +  ' stroke-dasharray="' + len + ' ' + c + '"'
          +  ' stroke-dashoffset="' + (-offset) + '"'
          +  ' transform="rotate(-90 ' + (size/2) + ' ' + (size/2) + ')"'
          +  ' stroke-linecap="butt"/>';
      offset += len;
    }
    if (showCenter && segments.length) {
      var pct = Math.round((segments[0].value / total) * 100);
      out += '<text x="' + (size/2) + '" y="' + (size/2 - 4) + '" text-anchor="middle"'
          +  ' font-family="var(--font-mono)" font-size="22" font-weight="600" fill="var(--text)">'
          +  pct + '%</text>';
      out += '<text x="' + (size/2) + '" y="' + (size/2 + 14) + '" text-anchor="middle"'
          +  ' font-family="var(--font-sans)" font-size="9" fill="var(--text3)" letter-spacing=".8">'
          +  labelText + '</text>';
    }
    out += '</svg>';
    return out;
  }

  /**
   * 2D heatmap. Each cell colored by a value-to-color function (default: green→amber→red ramp).
   * @param {number[][]} grid    rows × cols of values 0..1 (or normalize internally)
   * @param {object}  [opts]
   *   cellW (12), cellH (14), gap (2)
   *   colorFn(v)  — value→css-color (default ramp uses --up/--warn/--down)
   *   normalize (true) — auto-scale grid to 0..1
   * @returns {string} SVG
   */
  function heatmap(grid, opts) {
    opts = opts || {};
    var cellW = opts.cellW || 12;
    var cellH = opts.cellH || 14;
    var gap   = opts.gap   != null ? opts.gap : 2;
    if (!grid || !grid.length || !grid[0] || !grid[0].length) {
      return '<svg width="0" height="0"></svg>';
    }
    var rows = grid.length, cols = grid[0].length;
    var w = cols * (cellW + gap) - gap;
    var h = rows * (cellH + gap) - gap;
    var lo = Infinity, hi = -Infinity;
    if (opts.normalize !== false) {
      for (var r = 0; r < rows; r++) for (var c = 0; c < cols; c++) {
        var v = grid[r][c];
        if (v == null || isNaN(v)) continue;
        if (v < lo) lo = v; if (v > hi) hi = v;
      }
      if (!isFinite(lo)) { lo = 0; hi = 1; }
    } else { lo = 0; hi = 1; }
    var span = hi - lo || 1;
    var colorFn = opts.colorFn || function(v) {
      if (v == null || isNaN(v)) return 'var(--bg3)';
      var n = (v - lo) / span;
      if (n < 0.33) return 'var(--up)';
      if (n < 0.66) return 'var(--warn)';
      return 'var(--down)';
    };
    var out = '<svg width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">';
    for (var rr = 0; rr < rows; rr++) {
      for (var cc = 0; cc < cols; cc++) {
        var x = cc * (cellW + gap);
        var y = rr * (cellH + gap);
        out += '<rect x="' + x + '" y="' + y + '" width="' + cellW + '" height="' + cellH
            +  '" rx="2" fill="' + colorFn(grid[rr][cc]) + '"/>';
      }
    }
    out += '</svg>';
    return out;
  }

  function _strHash(s) {
    var h = 0;
    for (var i = 0; i < s.length; i++) h = ((h << 5) - h) + s.charCodeAt(i) | 0;
    return h;
  }

  global.PWChart = { sparkline: sparkline, donut: donut, heatmap: heatmap };
  global.pwSparkline = sparkline;
  global.pwDonut     = donut;
  global.pwHeatmap   = heatmap;
})(window);
