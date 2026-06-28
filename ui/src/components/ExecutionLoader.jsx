import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

const PLAN_LIBRARY = {
  observascore: {
    icon: "🎯",
    label: "ObservaScore",
    estimateLabel: "AI maturity assessment",
    steps: [
      { icon: "🧭", label: "Preparing assessment", detail: "Normalizing selected tools and building the maturity-assessment request.", seconds: 5 },
      { icon: "🔌", label: "Connecting to tool APIs", detail: "Checking Prometheus, Grafana, Jaeger, Alertmanager, Elasticsearch/OpenSearch, and Splunk inputs.", seconds: 9 },
      { icon: "🕸️", label: "Extracting observability estate", detail: "Collecting metrics, alert rules, dashboards, datasources, traces, log signals, and receivers.", seconds: 14 },
      { icon: "📐", label: "Scoring maturity dimensions", detail: "Running deterministic rules across SLO maturity, alert quality, signal coverage, incident response, and modern tooling.", seconds: 10 },
      { icon: "✦", label: "Building AI advisor narrative", detail: "AI is reading deterministic findings and creating a leadership-ready explanation without changing the score.", seconds: 28, aiOnly: true },
      { icon: "📄", label: "Rendering report", detail: "Generating the HTML and JSON report artifacts.", seconds: 8 },
    ],
    microcopy: [
      "Looking for missing runbooks and alert hygiene gaps…",
      "Checking dashboard quality, panel units, and operational readiness…",
      "Comparing the estate against SRE and modern observability practices…",
      "Distilling deterministic findings into executive language…",
    ],
  },
  obscrawl: {
    icon: "🕷️",
    label: "ObsCrawl",
    estimateLabel: "Telemetry estate crawl",
    steps: [
      { icon: "🧰", label: "Preparing crawl", detail: "Filtering tools to supported collectors and shaping a backend-safe payload.", seconds: 5 },
      { icon: "🔥", label: "Crawling metrics and alerts", detail: "Reading Prometheus targets, rules, recording rules, metric names, and alert definitions.", seconds: 10 },
      { icon: "📊", label: "Crawling dashboards", detail: "Collecting Grafana dashboards, panels, datasources, folders, and ownership hints.", seconds: 10 },
      { icon: "🔍", label: "Spelunking traces and logs", detail: "Checking trace services, operations, Elasticsearch/OpenSearch indices, and Splunk knowledge objects.", seconds: 12 },
      { icon: "📦", label: "Building workbook", detail: "Packaging the discovered telemetry estate into downloadable workbook/report artifacts.", seconds: 8 },
    ],
    microcopy: [
      "Spelunking dashboards, datasources, alert rules, and services…",
      "Deduplicating tool metadata before report generation…",
      "Collecting inventory from each reachable observability endpoint…",
      "Preparing the estate snapshot for download…",
    ],
  },
  rca: {
    icon: "🔍",
    label: "RCA Agent",
    estimateLabel: "Incident investigation",
    steps: [
      { icon: "🧾", label: "Preparing incident context", detail: "Reading service, alert name, description, and selected lookback window.", seconds: 5 },
      { icon: "🔔", label: "Collecting alert evidence", detail: "Checking firing alerts, alert rules, severity, labels, and alert annotations.", seconds: 9 },
      { icon: "📈", label: "Correlating metrics", detail: "Looking for latency, traffic, error-rate, saturation, and target-health anomalies.", seconds: 12 },
      { icon: "🧵", label: "Spelunking traces and logs", detail: "Scanning trace operations, slow spans, error spans, and log evidence for related symptoms.", seconds: 14 },
      { icon: "🧠", label: "Reasoning over blast radius", detail: "Linking symptoms across services and ranking likely root-cause candidates.", seconds: 12 },
      { icon: "✦", label: "Writing AI RCA narrative", detail: "AI is summarizing deterministic evidence into a service-owner RCA explanation.", seconds: 22, aiOnly: true },
      { icon: "📄", label: "Rendering RCA report", detail: "Creating the RCA report with evidence, correlation, and recommendations.", seconds: 8 },
    ],
    microcopy: [
      "Following the incident trail across metrics, logs, traces, and alerts…",
      "Looking for symptoms that line up in the same time window…",
      "Separating noisy signals from likely causal evidence…",
      "Building a concise RCA story for service owners…",
    ],
  },
  red: {
    icon: "🧪",
    label: "RED Intelligence",
    estimateLabel: "RED signal analysis",
    steps: [
      { icon: "🧭", label: "Preparing RED analysis", detail: "Selecting request, error, and duration signals from validated tools.", seconds: 5 },
      { icon: "📥", label: "Collecting request signals", detail: "Querying service traffic, request rates, operation volumes, and dashboard evidence.", seconds: 10 },
      { icon: "🚨", label: "Collecting error signals", detail: "Checking alert context, error logs, failed spans, and failed HTTP status evidence.", seconds: 10 },
      { icon: "⏱️", label: "Collecting duration signals", detail: "Inspecting latency metrics, slow spans, and service-level performance indicators.", seconds: 12 },
      { icon: "📊", label: "Building RED report", detail: "Ranking service health, hotspots, and action items into the report.", seconds: 8 },
    ],
    microcopy: [
      "Reading request, error, and duration evidence…",
      "Looking for services with traffic but weak RED coverage…",
      "Checking whether dashboards and alerts support service health decisions…",
      "Building a prioritized service health view…",
    ],
  },
  gapmap: {
    icon: "🗺️",
    label: "Gap Map",
    estimateLabel: "Coverage gap mapping",
    steps: [
      { icon: "🏷️", label: "Mapping application context", detail: "Reading application name, environment, services, and auto-discovery settings.", seconds: 5 },
      { icon: "🧩", label: "Discovering services", detail: "Combining user-provided services with tool-discovered services and dependencies.", seconds: 10 },
      { icon: "📡", label: "Checking signal coverage", detail: "Comparing metrics, logs, traces, dashboards, and alerts for every service.", seconds: 14 },
      { icon: "⚠️", label: "Finding observability gaps", detail: "Identifying missing telemetry, missing dashboards, weak alerting, and ownership gaps.", seconds: 12 },
      { icon: "📄", label: "Rendering gap map", detail: "Creating the service coverage matrix and report artifacts.", seconds: 8 },
    ],
    microcopy: [
      "Walking the application topology…",
      "Looking for services with metrics but no traces, or traces but no alerts…",
      "Checking tool coverage service by service…",
      "Turning coverage gaps into an actionable map…",
    ],
  },
  slo: {
    icon: "📏",
    label: "SLO Studio",
    estimateLabel: "SLO intelligence package",
    steps: [
      { icon: "🛰️", label: "Discovering services", detail: "Reading Prometheus labels and Jaeger/Tempo service-map evidence.", seconds: 8 },
      { icon: "📈", label: "Querying historical SLIs", detail: "Looking back across latency, availability, error-rate, traffic, and service behavior.", seconds: 18 },
      { icon: "🧾", label: "Detecting existing SLOs", detail: "Checking rules, dashboards, alerts, and Sloth-style SLO definitions already present.", seconds: 10 },
      { icon: "🎯", label: "Recommending missing SLOs", detail: "Ranking missing availability, latency, and error-rate SLOs based on evidence and confidence.", seconds: 12 },
      { icon: "✦", label: "Preparing AI advisor", detail: "AI is explaining deterministic SLO evidence for leadership and service owners.", seconds: 20, aiOnly: true },
      { icon: "📄", label: "Rendering SLO package", detail: "Generating HTML, JSON, and Sloth YAML artifacts.", seconds: 8 },
    ],
    microcopy: [
      "Comparing existing SLO coverage against observed behavior…",
      "Looking for user-facing SLIs with strong telemetry evidence…",
      "Checking 30-day trends before suggesting objectives…",
      "Preparing service-owner rollout guidance…",
    ],
  },
  ayosa: {
    icon: "🧠",
    label: "AYOSA",
    estimateLabel: "Interactive investigation",
    steps: [
      { icon: "💬", label: "Understanding the question", detail: "Parsing the investigation request, service hint, and time range.", seconds: 5 },
      { icon: "🧰", label: "Selecting tools", detail: "Choosing the best validated tools for metrics, logs, traces, dashboards, and alerts.", seconds: 6 },
      { icon: "📡", label: "Collecting evidence", detail: "Querying the observability stack for facts relevant to the question.", seconds: 14 },
      { icon: "🕵️", label: "Spelunking signals", detail: "Following related metrics, alerts, logs, traces, and dashboards across services.", seconds: 14 },
      { icon: "✦", label: "Writing investigation answer", detail: "AI is turning collected evidence into a readable answer with next steps.", seconds: 22, aiOnly: true },
      { icon: "✅", label: "Finalizing response", detail: "Preparing findings, evidence cards, and optional runbook actions.", seconds: 8 },
    ],
    microcopy: [
      "Looking across your observability stack for the strongest evidence…",
      "Spelunking for correlated symptoms and useful dashboards…",
      "Filtering generic noise so the answer stays actionable…",
      "Preparing a concise investigation summary…",
    ],
  },
  generic: {
    icon: "⚙️",
    label: "Execution",
    estimateLabel: "Backend execution",
    steps: [
      { icon: "🧭", label: "Preparing request", detail: "Normalizing inputs and selected tools.", seconds: 5 },
      { icon: "🔌", label: "Connecting to tools", detail: "Collecting observability evidence from configured endpoints.", seconds: 12 },
      { icon: "📊", label: "Analyzing evidence", detail: "Running deterministic checks and organizing findings.", seconds: 14 },
      { icon: "✦", label: "Generating AI explanation", detail: "AI is explaining deterministic findings and prioritizing next actions.", seconds: 22, aiOnly: true },
      { icon: "📄", label: "Rendering output", detail: "Creating report artifacts.", seconds: 8 },
    ],
    microcopy: [
      "Collecting signals from the observability estate…",
      "Checking for evidence-backed findings…",
      "Preparing a report that is useful for engineers and leaders…",
      "Almost there — packaging the result…",
    ],
  },
};

function getPlan(accelerator, mode) {
  const base = PLAN_LIBRARY[accelerator] || PLAN_LIBRARY.generic;
  const includeAi = String(mode || "").toLowerCase().includes("ai");
  const steps = base.steps.filter((step) => includeAi || !step.aiOnly);
  return { ...base, steps };
}

function computeActiveStep(steps, elapsed) {
  let cursor = 0;
  for (let index = 0; index < steps.length; index += 1) {
    cursor += steps[index].seconds || 8;
    if (elapsed < cursor) return index;
  }
  return Math.max(0, steps.length - 1);
}

function formatClock(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  const mins = Math.floor(safe / 60);
  const secs = String(safe % 60).padStart(2, "0");
  return `${mins}:${secs}`;
}

function formatRemaining(seconds) {
  const safe = Math.max(0, Number(seconds) || 0);
  if (safe <= 0) return "0s left";
  if (safe < 60) return `${safe}s left`;
  const mins = Math.floor(safe / 60);
  const secs = safe % 60;
  return secs ? `${mins}m ${secs}s left` : `${mins}m left`;
}

export default function ExecutionLoader({
  running,
  accelerator = "generic",
  mode = "deterministic",
  toolCount = 0,
  title,
  subtitle,
  estimatedSeconds,
}) {
  const [elapsed, setElapsed] = useState(0);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!running) {
      setElapsed(0);
      return undefined;
    }

    const startedAt = Date.now();
    setElapsed(0);
    const timer = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);

    return () => window.clearInterval(timer);
  }, [running]);

  useEffect(() => {
    if (!running || typeof document === "undefined") return undefined;

    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, [running]);

  const plan = useMemo(() => getPlan(accelerator, mode), [accelerator, mode]);

  if (!running || !mounted || typeof document === "undefined") return null;

  const totalSeconds = plan.steps.reduce((sum, step) => sum + (step.seconds || 8), 0);
  const estimate = Math.max(10, Number(estimatedSeconds) || totalSeconds);
  const remaining = Math.max(0, estimate - elapsed);
  const overEstimate = elapsed > estimate;
  const activeIndex = computeActiveStep(plan.steps, elapsed);
  const activeStep = plan.steps[activeIndex] || plan.steps[0];
  const progress = Math.min(99, Math.max(6, Math.round((elapsed / Math.max(estimate, 1)) * 100)));
  const microcopy = plan.microcopy[elapsed % plan.microcopy.length];
  const modeLabel = String(mode || "deterministic").replaceAll("-", " ");

  const overlay = (
    <div className="execution-loader-screen" role="status" aria-live="polite" aria-label="Execution in progress">
      <div className="execution-loader-backdrop-glow" />

      <div className="execution-loader-panel animate-in">
        <div className="execution-loader-header">
          <div className="execution-loader-brand">
            <span className="execution-loader-brand-icon">{plan.icon}</span>
            <div>
              <div className="execution-loader-kicker">{plan.estimateLabel}</div>
              <div className="execution-loader-heading">{title || `${plan.label} is working`}</div>
              <div className="execution-loader-subheading">
                {subtitle || `${toolCount || 0} tool${toolCount === 1 ? "" : "s"} selected · ${modeLabel}`}
              </div>
            </div>
          </div>

          <div className="execution-loader-countdown-card">
            <span className="execution-loader-countdown-label">Estimated time</span>
            <strong>{overEstimate ? "Finalizing…" : formatRemaining(remaining)}</strong>
            <span className="execution-loader-countdown-sub">elapsed {formatClock(elapsed)}</span>
          </div>
        </div>

        <div className="execution-loader-progress-wrap">
          <div className="execution-loader-progress-meta">
            <span>{overEstimate ? "Backend is still working" : "Execution progress"}</span>
            <span>{progress}%</span>
          </div>
          <div className="execution-loader-progress-shell">
            <div className="execution-loader-progress-bar" style={{ width: `${progress}%` }} />
          </div>
        </div>

        <div className="execution-loader-current-card">
          <div className="execution-loader-live-dot" />
          <div className="execution-loader-current-icon">{activeStep.icon}</div>
          <div className="execution-loader-current-copy">
            <div className="execution-loader-current-title">{activeStep.label}</div>
            <div className="execution-loader-current-detail">{activeStep.detail}</div>
            <div className="execution-loader-microcopy">{overEstimate ? "The estimate is based on prior runs. The backend is still finalizing the response." : microcopy}</div>
          </div>
        </div>

        <div className="execution-loader-step-grid">
          {plan.steps.map((step, index) => {
            const state = index < activeIndex ? "done" : index === activeIndex ? "active" : "todo";
            return (
              <div key={`${step.label}-${index}`} className={`execution-loader-step-card ${state}`}>
                <span className="execution-loader-step-index">{index < activeIndex ? "✓" : index + 1}</span>
                <span className="execution-loader-step-icon">{step.icon}</span>
                <span className="execution-loader-step-label">{step.label}</span>
              </div>
            );
          })}
        </div>

        <div className="execution-loader-footer-note">
          Keep this window open. The report or response will appear automatically when the backend finishes.
        </div>
      </div>
    </div>
  );

  return createPortal(overlay, document.body);
}
