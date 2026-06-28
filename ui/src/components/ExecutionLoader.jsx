import { useEffect, useMemo, useState } from "react";

const PLAN_LIBRARY = {
  observascore: {
    icon: "🎯",
    label: "ObservaScore",
    steps: [
      { icon: "🧭", label: "Preparing assessment", detail: "Normalizing selected tools and building the maturity-assessment request.", seconds: 5 },
      { icon: "🔌", label: "Connecting to tool APIs", detail: "Checking Prometheus, Grafana, Jaeger, Alertmanager, Elasticsearch/OpenSearch, and Splunk inputs.", seconds: 9 },
      { icon: "🕸️", label: "Extracting observability estate", detail: "Collecting metrics, alert rules, dashboards, datasources, traces, log signals, and receivers.", seconds: 12 },
      { icon: "📐", label: "Scoring maturity dimensions", detail: "Running deterministic rules across SLO maturity, alert quality, signal coverage, incident response, and modern tooling.", seconds: 10 },
      { icon: "✦", label: "Building AI advisor narrative", detail: "AI is reading deterministic findings and creating a leadership-ready explanation without changing the score.", seconds: 18, aiOnly: true },
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
    steps: [
      { icon: "🧾", label: "Preparing incident context", detail: "Reading service, alert name, description, and selected lookback window.", seconds: 5 },
      { icon: "🔔", label: "Collecting alert evidence", detail: "Checking firing alerts, alert rules, severity, labels, and alert annotations.", seconds: 9 },
      { icon: "📈", label: "Correlating metrics", detail: "Looking for latency, traffic, error-rate, saturation, and target-health anomalies.", seconds: 12 },
      { icon: "🧵", label: "Spelunking traces and logs", detail: "Scanning trace operations, slow spans, error spans, and log evidence for related symptoms.", seconds: 14 },
      { icon: "🧠", label: "Reasoning over blast radius", detail: "Linking symptoms across services and ranking likely root-cause candidates.", seconds: 12 },
      { icon: "✦", label: "Writing AI RCA narrative", detail: "AI is summarizing deterministic evidence into a service-owner RCA explanation.", seconds: 18, aiOnly: true },
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
    steps: [
      { icon: "🛰️", label: "Discovering services", detail: "Reading Prometheus labels and Jaeger/Tempo service-map evidence.", seconds: 8 },
      { icon: "📈", label: "Querying historical SLIs", detail: "Looking back across latency, availability, error-rate, traffic, and service behavior.", seconds: 16 },
      { icon: "🧾", label: "Detecting existing SLOs", detail: "Checking rules, dashboards, alerts, and Sloth-style SLO definitions already present.", seconds: 10 },
      { icon: "🎯", label: "Recommending missing SLOs", detail: "Ranking missing availability, latency, and error-rate SLOs based on evidence and confidence.", seconds: 12 },
      { icon: "✦", label: "Preparing AI advisor", detail: "AI is explaining deterministic SLO evidence for leadership and service owners.", seconds: 16, aiOnly: true },
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
    steps: [
      { icon: "💬", label: "Understanding the question", detail: "Parsing the investigation request, service hint, and time range.", seconds: 5 },
      { icon: "🧰", label: "Selecting tools", detail: "Choosing the best validated tools for metrics, logs, traces, dashboards, and alerts.", seconds: 6 },
      { icon: "📡", label: "Collecting evidence", detail: "Querying the observability stack for facts relevant to the question.", seconds: 14 },
      { icon: "🕵️", label: "Spelunking signals", detail: "Following related metrics, alerts, logs, traces, and dashboards across services.", seconds: 14 },
      { icon: "✦", label: "Writing investigation answer", detail: "AI is turning collected evidence into a readable answer with next steps.", seconds: 18, aiOnly: true },
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
    steps: [
      { icon: "🧭", label: "Preparing request", detail: "Normalizing inputs and selected tools.", seconds: 5 },
      { icon: "🔌", label: "Connecting to tools", detail: "Collecting observability evidence from configured endpoints.", seconds: 12 },
      { icon: "📊", label: "Analyzing evidence", detail: "Running deterministic checks and organizing findings.", seconds: 14 },
      { icon: "✦", label: "Generating AI explanation", detail: "AI is explaining deterministic findings and prioritizing next actions.", seconds: 18, aiOnly: true },
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
  return steps.length - 1;
}

function formatElapsed(seconds) {
  const mins = Math.floor(seconds / 60);
  const secs = String(seconds % 60).padStart(2, "0");
  return `${mins}:${secs}`;
}

export default function ExecutionLoader({
  running,
  accelerator = "generic",
  mode = "deterministic",
  toolCount = 0,
  title,
  subtitle,
}) {
  const [elapsed, setElapsed] = useState(0);

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

  const plan = useMemo(() => getPlan(accelerator, mode), [accelerator, mode]);

  if (!running) return null;

  const activeIndex = computeActiveStep(plan.steps, elapsed);
  const totalSeconds = plan.steps.reduce((sum, step) => sum + (step.seconds || 8), 0);
  const progress = Math.min(96, Math.max(8, Math.round((elapsed / Math.max(totalSeconds, 1)) * 100)));
  const activeStep = plan.steps[activeIndex] || plan.steps[0];
  const microcopy = plan.microcopy[elapsed % plan.microcopy.length];

  return (
    <div className="execution-loader animate-in" role="status" aria-live="polite">
      <div className="execution-loader-topline">
        <div className="execution-loader-title-wrap">
          <span className="execution-loader-orb">{plan.icon}</span>
          <div>
            <div className="execution-loader-title">
              {title || `${plan.label} is working`}
            </div>
            <div className="execution-loader-subtitle">
              {subtitle || `${toolCount || 0} tool${toolCount === 1 ? "" : "s"} selected · ${String(mode || "deterministic").replaceAll("-", " ")}`}
            </div>
          </div>
        </div>
        <div className="execution-loader-timer">{formatElapsed(elapsed)}</div>
      </div>

      <div className="execution-loader-progress-shell">
        <div className="execution-loader-progress-bar" style={{ width: `${progress}%` }} />
      </div>

      <div className="execution-loader-current">
        <span className="execution-loader-pulse" />
        <div>
          <div className="execution-loader-current-title">
            {activeStep.icon} {activeStep.label}
          </div>
          <div className="execution-loader-current-detail">{activeStep.detail}</div>
          <div className="execution-loader-microcopy">{microcopy}</div>
        </div>
      </div>

      <div className="execution-loader-steps">
        {plan.steps.map((step, index) => {
          const state = index < activeIndex ? "done" : index === activeIndex ? "active" : "todo";
          return (
            <div key={`${step.label}-${index}`} className={`execution-loader-step ${state}`}>
              <span className="execution-loader-step-dot">{index < activeIndex ? "✓" : index + 1}</span>
              <span className="execution-loader-step-label">{step.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
