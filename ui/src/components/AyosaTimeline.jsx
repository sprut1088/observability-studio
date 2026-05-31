/**
 * AyosaTimeline — chronological incident timeline for AYOSA investigation results.
 */
function fmtTimestamp(ts) {
  if (!ts) return null;
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

const SEVERITY_CLASS = {
  critical: "ayosa-tl-sev-critical",
  high:     "ayosa-tl-sev-high",
  warning:  "ayosa-tl-sev-warning",
  warn:     "ayosa-tl-sev-warning",
  info:     "ayosa-tl-sev-info",
  event:    "ayosa-tl-sev-info",
};

export default function AyosaTimeline({ items }) {
  if (!items || items.length === 0) return null;

  return (
    <div className="ayosa-timeline">
      {items.map((item, i) => {
        const sevClass = SEVERITY_CLASS[(item.severity || "").toLowerCase()] || "ayosa-tl-sev-info";
        return (
          <div className="ayosa-timeline-item" key={`${item.timestamp}-${i}`}>
            <div className="ayosa-timeline-top">
              <strong>{item.source}</strong>
              {item.severity && <span className={sevClass}>{item.severity}</span>}
            </div>
            {item.timestamp && <small>{fmtTimestamp(item.timestamp)}</small>}
            <p>{item.event}</p>
          </div>
        );
      })}
    </div>
  );
}
