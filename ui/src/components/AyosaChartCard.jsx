/**
 * AyosaChartCard — lightweight SVG chart for AYOSA investigation results.
 * Supports line charts, bar charts, and single-value stat cards.
 * No external chart library required.
 */

function fmtVal(v) {
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `${(v / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(v / 1_000).toFixed(1)}k`;
  if (abs >= 0.001 || v === 0) return parseFloat(v.toPrecision(3)).toString();
  return v.toExponential(1);
}

function fmtTime(ts) {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return `${d.getHours().toString().padStart(2, "0")}:${d
    .getMinutes()
    .toString()
    .padStart(2, "0")}`;
}

const W = 400;
const H = 100;
const PAD = { top: 8, right: 12, bottom: 22, left: 42 };
const iW = W - PAD.left - PAD.right;
const iH = H - PAD.top - PAD.bottom;

function parseSeries(data) {
  return data
    .map((d) => ({ t: new Date(d.timestamp).getTime(), v: parseFloat(d.value) }))
    .filter((d) => !isNaN(d.t) && !isNaN(d.v));
}

function SvgLineChart({ data }) {
  const parsed = parseSeries(data);
  if (parsed.length < 2) return <div className="ayosa-chart-empty">Insufficient data</div>;

  const tMin = Math.min(...parsed.map((d) => d.t));
  const tMax = Math.max(...parsed.map((d) => d.t));
  const vMin = Math.min(...parsed.map((d) => d.v));
  const vMax = Math.max(...parsed.map((d) => d.v));
  const vRange = vMax - vMin || 1;
  const tRange = tMax - tMin || 1;

  const toX = (t) => PAD.left + ((t - tMin) / tRange) * iW;
  const toY = (v) => PAD.top + (1 - (v - vMin) / vRange) * iH;

  const pts = parsed.map((d) => `${toX(d.t).toFixed(1)},${toY(d.v).toFixed(1)}`).join(" ");
  const firstX = toX(parsed[0].t).toFixed(1);
  const lastX = toX(parsed[parsed.length - 1].t).toFixed(1);
  const bottomY = (PAD.top + iH).toFixed(1);
  const areaPts = `${firstX},${bottomY} ${pts} ${lastX},${bottomY}`;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="ayosa-chart-svg" aria-hidden="true">
      {[0, 0.5, 1].map((r, i) => (
        <line
          key={i}
          x1={PAD.left}
          x2={PAD.left + iW}
          y1={PAD.top + r * iH}
          y2={PAD.top + r * iH}
          stroke="currentColor"
          strokeWidth="0.5"
          opacity="0.1"
        />
      ))}
      <polygon points={areaPts} fill="var(--violet)" fillOpacity="0.07" />
      <polyline
        points={pts}
        fill="none"
        stroke="var(--violet)"
        strokeWidth="2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
      <text x={PAD.left - 4} y={PAD.top + 4} textAnchor="end" fontSize="9" fill="currentColor" opacity="0.45">
        {fmtVal(vMax)}
      </text>
      <text x={PAD.left - 4} y={PAD.top + iH + 1} textAnchor="end" fontSize="9" fill="currentColor" opacity="0.45">
        {fmtVal(vMin)}
      </text>
      <text x={PAD.left} y={H - 4} textAnchor="start" fontSize="9" fill="currentColor" opacity="0.45">
        {fmtTime(tMin)}
      </text>
      <text x={PAD.left + iW} y={H - 4} textAnchor="end" fontSize="9" fill="currentColor" opacity="0.45">
        {fmtTime(tMax)}
      </text>
    </svg>
  );
}

function SvgBarChart({ data }) {
  const parsed = parseSeries(data);
  if (parsed.length < 1) return <div className="ayosa-chart-empty">Insufficient data</div>;

  const tMin = Math.min(...parsed.map((d) => d.t));
  const tMax = Math.max(...parsed.map((d) => d.t));
  const vMax = Math.max(...parsed.map((d) => d.v), 1);
  const tRange = tMax - tMin || 1;
  const barW = Math.max(2, iW / parsed.length - 2);
  const bottomY = PAD.top + iH;

  const toX = (t) => PAD.left + ((t - tMin) / tRange) * iW;
  const toBarH = (v) => (v / vMax) * iH;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="ayosa-chart-svg" aria-hidden="true">
      {[0.5, 1].map((r, i) => (
        <line
          key={i}
          x1={PAD.left}
          x2={PAD.left + iW}
          y1={PAD.top + (1 - r) * iH}
          y2={PAD.top + (1 - r) * iH}
          stroke="currentColor"
          strokeWidth="0.5"
          opacity="0.1"
        />
      ))}
      {parsed.map((d, i) => {
        const x = toX(d.t) - barW / 2;
        const bH = Math.max(1, toBarH(d.v));
        const y = bottomY - bH;
        return (
          <rect
            key={i}
            x={x.toFixed(1)}
            y={y.toFixed(1)}
            width={barW.toFixed(1)}
            height={bH.toFixed(1)}
            fill="var(--violet)"
            fillOpacity="0.65"
            rx="1"
          />
        );
      })}
      <text x={PAD.left - 4} y={PAD.top + 4} textAnchor="end" fontSize="9" fill="currentColor" opacity="0.45">
        {fmtVal(vMax)}
      </text>
      <text x={PAD.left} y={H - 4} textAnchor="start" fontSize="9" fill="currentColor" opacity="0.45">
        {fmtTime(tMin)}
      </text>
      <text x={PAD.left + iW} y={H - 4} textAnchor="end" fontSize="9" fill="currentColor" opacity="0.45">
        {fmtTime(tMax)}
      </text>
    </svg>
  );
}

function StatCard({ data }) {
  const point = data[0];
  const val = parseFloat(point.value);
  return (
    <div className="ayosa-chart-stat">
      <span className="ayosa-chart-stat-value">{isNaN(val) ? "—" : fmtVal(val)}</span>
      {point.timestamp && <span className="ayosa-chart-stat-ts">{point.timestamp}</span>}
    </div>
  );
}

export default function AyosaChartCard({ chart }) {
  const { title, type = "line", signal = "", source = "", data = [] } = chart;
  const signalClass = `ayosa-chart-badge ayosa-chart-badge-${signal}`;

  return (
    <div className="ayosa-chart">
      <div className="ayosa-chart-header">
        <div className="ayosa-chart-title">{title}</div>
        <div className="ayosa-chart-badges">
          <span className={signalClass}>{signal}</span>
          <span className="ayosa-chart-badge">{source}</span>
        </div>
      </div>

      {!data || data.length === 0 ? (
        <div className="ayosa-chart-empty">No data available</div>
      ) : data.length === 1 ? (
        <StatCard data={data} />
      ) : type === "bar" ? (
        <SvgBarChart data={data} />
      ) : (
        <SvgLineChart data={data} />
      )}
    </div>
  );
}
