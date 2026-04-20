import { useState, useEffect } from "react";
import CrawlModal from "./CrawlModal";
import AssessModal from "./AssessModal";
import RCAModal from "./RCAModal";
import { getFeatureFlags } from "../api";

/* ── Tile definitions ───────────────────────────────────── */
const TILES = [
  {
    id: "obscrawl",
    icon: "🕷️",
    title: "ObsCrawl",
    tagline: "Crawl & Export",
    description:
      "Connect to any observability tool, validate the endpoint, then extract the full telemetry estate and download it as a structured Excel workbook.",
    accentClass: "tile-teal",
    features: ["Tool connectivity probe", "Adapter-driven extraction", "Multi-sheet Excel export"],
    badge: "Crawler",
    badgeClass: "badge-teal",
  },
  {
    id: "observascore",
    icon: "🎯",
    title: "ObservaScore",
    tagline: "Assess & Score",
    description:
      "Run the full observability maturity assessment against your tool stack. Choose deterministic rules-only scoring or enrich results with AI-powered gap analysis.",
    accentClass: "tile-indigo",
    features: ["35+ scoring rules", "10 maturity dimensions", "Optional AI gap analysis"],
    badge: "Assessment",
    badgeClass: "badge-indigo",
  },
  {
    id: "rca_agent",
    icon: "🔍",
    title: "RCA Agent",
    tagline: "Analyse & Investigate",
    description:
      "Automatically collect signals from Prometheus, Grafana, Jaeger, and OpenSearch during an incident. The agent correlates anomalies, maps the blast radius, and generates a Claude-powered Root Cause Analysis report.",
    accentClass: "tile-amber",
    features: ["Multi-tool signal collection", "Anomaly correlation engine", "Claude AI RCA report"],
    badge: "RCA",
    badgeClass: "badge-amber",
  },
];

/* ══════════════════════════════════════════════════════════
   HubPage
══════════════════════════════════════════════════════════ */
export default function HubPage() {
  const [activeTile, setActiveTile] = useState(null); // "obscrawl" | "observascore" | null
  // Feature flags — default all true so tiles show during initial load
  const [flags, setFlags] = useState({ observascore: true, obscrawl: true, rca_agent: true });

  useEffect(() => {
    getFeatureFlags()
      .then((res) => setFlags(res.data || {}))
      .catch(() => {
        // If the endpoint is unreachable, keep defaults (show everything)
      });
  }, []);

  // Only render tiles whose feature flag is enabled
  const visibleTiles = TILES.filter((tile) => flags[tile.id] !== false);


  return (
    <div className="hub-wrapper">
      {/* ── Section label ─────────────────────────────── */}
      <div className="hub-section-label">
        <span className="hub-section-line" />
        <span className="hub-section-text">Choose a module</span>
        <span className="hub-section-line" />
      </div>

      {/* ── Tile grid ─────────────────────────────────── */}
      <div className="hub-grid">
        {visibleTiles.map((tile) => (
          <button
            key={tile.id}
            className={`hub-tile ${tile.accentClass}`}
            onClick={() => setActiveTile(tile.id)}
            aria-label={`Open ${tile.title}`}
          >
            {/* Top row */}
            <div className="hub-tile-top">
              <span className={`hub-tile-badge ${tile.badgeClass}`}>{tile.badge}</span>
            </div>

            {/* Icon + title */}
            <div className="hub-tile-icon-wrap">
              <span className="hub-tile-icon">{tile.icon}</span>
            </div>
            <div className="hub-tile-title">{tile.title}</div>
            <div className="hub-tile-tagline">{tile.tagline}</div>

            {/* Description */}
            <p className="hub-tile-desc">{tile.description}</p>

            {/* Feature list */}
            <ul className="hub-tile-features">
              {tile.features.map((f) => (
                <li key={f}>
                  <span className="hub-feature-dot" />
                  {f}
                </li>
              ))}
            </ul>

            {/* CTA */}
            <div className={`hub-tile-cta ${tile.accentClass}-cta`}>
              Open {tile.title} →
            </div>
          </button>
        ))}
      </div>

      {/* ── Modals ────────────────────────────────────── */}
      {activeTile === "obscrawl" && (
        <CrawlModal onClose={() => setActiveTile(null)} />
      )}
      {activeTile === "observascore" && (
        <AssessModal onClose={() => setActiveTile(null)} />
      )}
      {activeTile === "rca_agent" && (
        <RCAModal onClose={() => setActiveTile(null)} />
      )}
    </div>
  );
}
