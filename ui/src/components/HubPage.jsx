import { useState, useEffect } from "react";
import CrawlModal from "./CrawlModal";
import AssessModal from "./AssessModal";
import RCAModal from "./RCAModal";
import RedIntelligenceModal from "./RedIntelligenceModal";
import GapMapModal from "./GapMapModal";
import IncidentSimulatorModal from "./IncidentSimulatorModal";
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
  {
    id: "red_panel_intelligence",
    icon: "📉",
    title: "RED Panel Intelligence",
    tagline: "Dashboard RED Coverage",
    description:
      "Analyze dashboards across Grafana, Splunk, Datadog, Dynatrace, and AppDynamics for Rate, Errors, and Duration coverage. Detect weak panels, missing queries, and incomplete service views.",
    accentClass: "tile-rose",
    features: ["Multi-tool dashboard analysis", "RED coverage scoring", "Panel query intelligence"],
    badge: "Dashboard Quality",
    badgeClass: "badge-rose",
  },
  {
    id: "observability_gap_map",
    icon: "🧭",
    title: "Observability Gap Map",
    tagline: "Application Service Coverage",
    description:
      "Map observability coverage for a specific application and its services across metrics, logs, traces, dashboards, alerts, and RED readiness.",
    accentClass: "tile-cyan",
    features: [
      "Application-scoped service inventory",
      "Interactive signal coverage matrix",
      "Auto-discovery suggestions with noise filtering",
    ],
    badge: "BLIND SPOT ANALYSIS",
    badgeClass: "badge-cyan",
  },
  {
    id: "incident_simulator",
    icon: "🎲",
    title: "Incident Readiness Simulator",
    tagline: "Test & Validate",
    description:
      "Simulate production incidents for a selected application service and score readiness across detection, visibility, diagnosis, and response capabilities.",
    accentClass: "tile-amber",
    features: ["Deterministic readiness scoring", "Detection-to-response timeline", "Optional AI executive summary"],
    badge: "TEST & VALIDATE",
    badgeClass: "badge-amber",
  },
];

/* ══════════════════════════════════════════════════════════
   HubPage
══════════════════════════════════════════════════════════ */
export default function HubPage() {
  const [activeTile, setActiveTile] = useState(null); // "obscrawl" | "observascore" | null
  // Feature flags — default all true so tiles show during initial load
  const [flags, setFlags] = useState({
    observascore: true,
    obscrawl: true,
    rca_agent: true,
    red_panel_intelligence: true,
    observability_gap_map: true,
    incident_simulator: true,
  });

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
              {tile.id === "observability_gap_map" ? "Open Gap Map →" : `Open ${tile.title} →`}
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
      {activeTile === "red_panel_intelligence" && (
        <RedIntelligenceModal onClose={() => setActiveTile(null)} />
      )}
      {activeTile === "observability_gap_map" && (
        <GapMapModal onClose={() => setActiveTile(null)} />
      )}
      {activeTile === "incident_simulator" && (
        <IncidentSimulatorModal onClose={() => setActiveTile(null)} />
      )}
    </div>
  );
}
