/* FinHound dashboard: formatting helpers and a dependency-free canvas chart engine. */
"use strict";

window.FH = window.FH || {};

FH.esc = function (value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, (char) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char])
  );
};

FH.toNumber = function (value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};

const numberFormat = (digits) =>
  new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });

FH.fmt = {
  num(value, digits = 2) {
    const number = FH.toNumber(value);
    return number === null ? "—" : numberFormat(digits).format(number);
  },
  int(value) {
    const number = FH.toNumber(value);
    return number === null ? "—" : new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(number);
  },
  pct(value, digits = 2) {
    const number = FH.toNumber(value);
    return number === null ? "—" : `${numberFormat(digits).format(number * 100)}%`;
  },
  pctRaw(value, digits = 1) {
    const number = FH.toNumber(value);
    return number === null ? "—" : `${numberFormat(digits).format(number)}%`;
  },
  signed(value, digits = 2) {
    const number = FH.toNumber(value);
    if (number === null) return "—";
    return `${number > 0 ? "+" : ""}${numberFormat(digits).format(number)}`;
  },
  signedPct(value, digits = 2) {
    const number = FH.toNumber(value);
    if (number === null) return "—";
    return `${number > 0 ? "+" : ""}${numberFormat(digits).format(number * 100)}%`;
  },
  money(value, digits = 2) {
    const number = FH.toNumber(value);
    if (number === null) return "—";
    return `$${numberFormat(digits).format(number)}`;
  },
  compact(value, digits = 1) {
    const number = FH.toNumber(value);
    if (number === null) return "—";
    const abs = Math.abs(number);
    const units = [
      [1e12, "T"],
      [1e9, "B"],
      [1e6, "M"],
      [1e3, "K"],
    ];
    for (const [scale, suffix] of units) {
      if (abs >= scale) return `${numberFormat(digits).format(number / scale)}${suffix}`;
    }
    return numberFormat(digits).format(number);
  },
  date(value) {
    if (!value) return "—";
    // Date-only strings are UTC midnight; format them in UTC so they do not
    // shift to the previous day in western time zones.
    const text = String(value);
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(text);
    const date = new Date(dateOnly ? `${text}T00:00:00Z` : value);
    if (Number.isNaN(date.getTime())) return FH.esc(value);
    return date.toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "2-digit",
      ...(dateOnly ? { timeZone: "UTC" } : {}),
    });
  },
  datetime(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return FH.esc(value);
    return date.toLocaleString("en-US", {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  },
  shortId(value, length = 10) {
    return value ? String(value).slice(0, length) : "—";
  },
  json(value, max = 120) {
    if (value === null || value === undefined) return "—";
    let text;
    try {
      text = typeof value === "string" ? value : JSON.stringify(value);
    } catch {
      text = String(value);
    }
    if (!text) return "—";
    return text.length > max ? `${text.slice(0, max)}…` : text;
  },
};

FH.tone = function (value, invert = false) {
  const number = FH.toNumber(value);
  if (number === null) return "muted";
  if (number === 0) return "muted";
  const good = invert ? number < 0 : number > 0;
  return good ? "up" : "down";
};

FH.empty = function (message, hint = "") {
  return `<div class="empty"><strong>${FH.esc(message)}</strong>${hint ? `<div>${FH.esc(hint)}</div>` : ""}</div>`;
};

FH.kpi = function ({ label, value, sub = "", tone = "", title = "" }) {
  return `<div class="kpi ${FH.esc(tone)}"${title ? ` title="${FH.esc(title)}"` : ""}>
    <div class="kpi-label">${FH.esc(label)}</div>
    <div class="kpi-value">${value}</div>
    <div class="kpi-sub">${sub}</div>
  </div>`;
};

FH.listRow = function ({ rank, symbol, value, width, negative = false, meta = "" }) {
  const safeWidth = Math.max(2, Math.min(100, Number(width) || 0));
  return `<div class="list-row">
    <div class="lhs">
      ${rank !== undefined ? `<span class="rank">${FH.esc(rank)}</span>` : ""}
      <strong class="mono">${FH.esc(symbol)}</strong>
      ${meta ? `<span class="faint">${FH.esc(meta)}</span>` : ""}
    </div>
    <div style="display:flex;align-items:center;gap:10px;min-width:150px;justify-content:flex-end">
      <div class="bar-track" style="width:84px"><div class="bar-fill ${negative ? "neg" : ""}" style="width:${safeWidth}%"></div></div>
      <span class="mono">${value}</span>
    </div>
  </div>`;
};

FH.badge = function (text, kind = "neutral") {
  return `<span class="badge ${FH.esc(kind)}">${FH.esc(text)}</span>`;
};

FH.severityBadge = function (severity) {
  const map = { error: "bad", warn: "warn", info: "neutral" };
  return FH.badge(severity || "info", map[String(severity).toLowerCase()] || "neutral");
};

const PALETTE = ["#2dd4bf", "#818cf8", "#60a5fa", "#fbbf24", "#f472b6", "#34d399", "#fb923c"];

FH.Chart = class {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.data = null;
    this.hover = null;
    this._pending = false;
    this._geometry = null;
    this.tooltip = document.createElement("div");
    this.tooltip.className = "chart-tooltip";
    if (canvas.parentElement) canvas.parentElement.appendChild(this.tooltip);
    canvas.addEventListener("mousemove", (event) => this._onMove(event));
    canvas.addEventListener("mouseleave", () => {
      this.hover = null;
      this.tooltip.style.display = "none";
      this.schedule();
    });
    const redraw = () => this.schedule();
    window.addEventListener("resize", redraw);
    if (window.ResizeObserver && canvas.parentElement) {
      new ResizeObserver(redraw).observe(canvas.parentElement);
    }
  }

  schedule() {
    if (this._pending) return;
    this._pending = true;
    requestAnimationFrame(() => {
      this._pending = false;
      this.render();
    });
  }

  setData(data) {
    this.data = data;
    this.hover = null;
    this.tooltip.style.display = "none";
    this.schedule();
  }

  clear() {
    this.data = null;
    this.tooltip.style.display = "none";
    this.schedule();
  }

  _onMove(event) {
    const data = this.data;
    const geometry = this._geometry;
    if (!data || !geometry) return;
    const rect = this.canvas.getBoundingClientRect();
    const mx = event.clientX - rect.left;
    const my = event.clientY - rect.top;
    const { pad, plotW, plotH, n } = geometry;
    if (mx < pad.l - 6 || mx > pad.l + plotW + 6 || my < pad.t - 6 || my > pad.t + plotH + 6) {
      this.hover = null;
      this.tooltip.style.display = "none";
      this.schedule();
      return;
    }
    let index;
    if (data.type === "bars") {
      index = Math.floor(((mx - pad.l) / plotW) * n);
    } else {
      index = n <= 1 ? 0 : Math.round(((mx - pad.l) / plotW) * (n - 1));
    }
    index = Math.max(0, Math.min(n - 1, index));
    this.hover = index;
    this._showTooltip(index, event);
    this.schedule();
  }

  _showTooltip(index, event) {
    const data = this.data;
    const container = this.canvas.parentElement;
    if (!data || !container) return;
    const label = data.labels[index] ?? "";
    const format = data.valueFormat || ((value) => FH.fmt.num(value, 2));
    const rows =
      data.type === "bars"
        ? `<div class="tt-row"><span class="tt-name"><span class="tt-swatch" style="background:${PALETTE[0]}"></span>${FH.esc(data.series[0]?.name || "Value")}</span><span class="tt-value">${format(data.series[0]?.values[index])}</span></div>`
        : data.series
            .map(
              (series) => `<div class="tt-row"><span class="tt-name"><span class="tt-swatch" style="background:${series.color || PALETTE[0]}"></span>${FH.esc(series.name)}</span><span class="tt-value">${format(series.values[index])}</span></div>`
            )
            .join("");
    this.tooltip.innerHTML = `<div class="tt-date">${FH.esc(data.labelFormat ? data.labelFormat(label) : label)}</div>${rows}`;
    this.tooltip.style.display = "block";
    const rect = container.getBoundingClientRect();
    const x = Math.max(6, Math.min(event.clientX - rect.left + 14, rect.width - this.tooltip.offsetWidth - 6));
    const y = Math.max(6, Math.min(event.clientY - rect.top - 12, rect.height - this.tooltip.offsetHeight - 6));
    this.tooltip.style.left = `${x}px`;
    this.tooltip.style.top = `${y}px`;
  }

  render() {
    const canvas = this.canvas;
    const ctx = this.ctx;
    if (!canvas || !ctx) return;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, rect.width);
    const height = Math.max(1, rect.height);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const data = this.data;
    if (!data || !data.labels || !data.labels.length || !data.series || !data.series.length) {
      this._geometry = null;
      return;
    }

    const pad = { l: 58, r: 16, t: 14, b: 24 };
    const plotW = Math.max(10, width - pad.l - pad.r);
    const plotH = Math.max(10, height - pad.t - pad.b);
    const n = data.labels.length;
    const values = data.series.flatMap((series) => series.values).filter((value) => Number.isFinite(value));
    if (!values.length) {
      this._geometry = null;
      return;
    }
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (data.zeroLine || data.includeZero || data.type === "bars") {
      min = Math.min(min, 0);
      max = Math.max(max, 0);
    }
    if (data.yMin !== undefined && data.yMin !== null) min = Math.min(min, data.yMin);
    if (data.yMax !== undefined && data.yMax !== null) max = Math.max(max, data.yMax);
    if (min === max) {
      const bump = Math.abs(max) || 1;
      min -= bump * 0.5;
      max += bump * 0.5;
    }
    const span = max - min;
    min -= span * 0.06;
    max += span * 0.06;
    const y = (value) => pad.t + plotH - ((value - min) / (max - min)) * plotH;
    const xLine = (index) => (n <= 1 ? pad.l + plotW / 2 : pad.l + (index / (n - 1)) * plotW);
    const tickFormat = data.yFormat || ((value) => FH.fmt.compact(value, 2));
    const labelFormat = data.labelFormat || ((value) => value);

    this._geometry = { pad, plotW, plotH, n };

    // Grid and y labels
    ctx.font = '10px "SFMono-Regular", ui-monospace, Menlo, monospace';
    ctx.textAlign = "right";
    ctx.textBaseline = "middle";
    const gridLines = 5;
    for (let i = 0; i <= gridLines; i += 1) {
      const value = min + ((max - min) * i) / gridLines;
      const py = y(value);
      ctx.strokeStyle = Math.abs(value) < 1e-12 ? "rgba(148,163,184,0.32)" : "rgba(148,163,184,0.1)";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(pad.l, py);
      ctx.lineTo(pad.l + plotW, py);
      ctx.stroke();
      ctx.fillStyle = "rgba(141,157,182,0.9)";
      ctx.fillText(tickFormat(value), pad.l - 9, py);
    }

    // X ticks
    ctx.textAlign = "center";
    ctx.textBaseline = "top";
    const ticks = Math.min(7, n);
    const seen = new Set();
    for (let i = 0; i < ticks; i += 1) {
      const index = ticks <= 1 ? 0 : Math.round(((n - 1) * i) / (ticks - 1));
      if (seen.has(index)) continue;
      seen.add(index);
      const px = data.type === "bars" ? pad.l + ((index + 0.5) / n) * plotW : xLine(index);
      ctx.fillStyle = "rgba(93,108,133,0.95)";
      ctx.fillText(labelFormat(data.labels[index]), px, pad.t + plotH + 8);
    }

    const zeroY = y(0);

    if (data.type === "bars") {
      const series = data.series[0];
      const step = plotW / n;
      const barWidth = Math.max(1, Math.min(step * 0.78, data.maxBarWidth || 34));
      const colors = data.barColors || null;
      series.values.forEach((value, index) => {
        if (!Number.isFinite(value)) return;
        const px = pad.l + (index + 0.5) * step;
        const py = y(value);
        const top = Math.min(py, zeroY);
        const barHeight = Math.max(1, Math.abs(py - zeroY));
        ctx.fillStyle = colors
          ? colors(value, index)
          : value >= 0
          ? "rgba(45,212,191,0.75)"
          : "rgba(248,113,113,0.75)";
        ctx.fillRect(px - barWidth / 2, top, barWidth, barHeight);
      });
      if (data.zeroLine) {
        ctx.strokeStyle = "rgba(148,163,184,0.35)";
        ctx.beginPath();
        ctx.moveTo(pad.l, zeroY);
        ctx.lineTo(pad.l + plotW, zeroY);
        ctx.stroke();
      }
    } else {
      data.series.forEach((series, seriesIndex) => {
        const color = series.color || PALETTE[seriesIndex % PALETTE.length];
        const points = [];
        series.values.forEach((value, index) => {
          if (Number.isFinite(value)) points.push([xLine(index), y(value)]);
        });
        if (!points.length) return;

        if (series.area || data.area) {
          const gradient = ctx.createLinearGradient(0, pad.t, 0, pad.t + plotH);
          gradient.addColorStop(0, hexToRgba(color, 0.3));
          gradient.addColorStop(1, hexToRgba(color, 0.01));
          ctx.beginPath();
          ctx.moveTo(points[0][0], y(Math.max(min, 0)));
          points.forEach(([px, py]) => ctx.lineTo(px, py));
          ctx.lineTo(points[points.length - 1][0], y(Math.max(min, 0)));
          ctx.closePath();
          ctx.fillStyle = gradient;
          ctx.fill();
        }

        ctx.beginPath();
        ctx.strokeStyle = color;
        ctx.lineWidth = series.width || 1.8;
        ctx.lineJoin = "round";
        ctx.lineCap = "round";
        if (series.dash) ctx.setLineDash(series.dash);
        points.forEach(([px, py], index) => {
          if (index === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.stroke();
        ctx.setLineDash([]);
      });

      if (data.zeroLine) {
        ctx.strokeStyle = "rgba(148,163,184,0.3)";
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(pad.l, zeroY);
        ctx.lineTo(pad.l + plotW, zeroY);
        ctx.stroke();
      }
    }

    // Hover crosshair and markers
    if (this.hover !== null && this.hover >= 0 && this.hover < n) {
      const index = this.hover;
      const px = data.type === "bars" ? pad.l + ((index + 0.5) / n) * plotW : xLine(index);
      ctx.strokeStyle = "rgba(226,239,255,0.28)";
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(px, pad.t);
      ctx.lineTo(px, pad.t + plotH);
      ctx.stroke();
      ctx.setLineDash([]);
      if (data.type !== "bars") {
        data.series.forEach((series, seriesIndex) => {
          const value = series.values[index];
          if (!Number.isFinite(value)) return;
          const color = series.color || PALETTE[seriesIndex % PALETTE.length];
          ctx.beginPath();
          ctx.arc(px, y(value), 3.1, 0, Math.PI * 2);
          ctx.fillStyle = color;
          ctx.fill();
          ctx.beginPath();
          ctx.arc(px, y(value), 5.2, 0, Math.PI * 2);
          ctx.strokeStyle = hexToRgba(color, 0.4);
          ctx.stroke();
        });
      }
    }
  }
};

function hexToRgba(hex, alpha) {
  if (!hex || !hex.startsWith("#")) return `rgba(45,212,191,${alpha})`;
  const normalized = hex.length === 4 ? `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}` : hex;
  const value = Number.parseInt(normalized.slice(1), 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r},${g},${b},${alpha})`;
}

FH.chart = function (canvasId) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;
  if (!canvas._fhChart) canvas._fhChart = new FH.Chart(canvas);
  return canvas._fhChart;
};

FH.COLORS = PALETTE;
