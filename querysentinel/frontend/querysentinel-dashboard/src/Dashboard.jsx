import React, { useState, useEffect, useCallback } from "react";

/**
 * QuerySentinel Dashboard (Week 4)
 * ====================================
 * Single-file React dashboard. Polls the FastAPI backend
 * (api/main.py) every 5 seconds for live updates.
 *
 * Design direction: terminal / database-monitoring aesthetic.
 * Dark background, monospace data, amber/green/red status
 * colors borrowed from real DB monitoring tools (pgAdmin,
 * Datadog APM) rather than a generic SaaS dashboard look —
 * because this tool's actual users are engineers staring at
 * query logs, not executives looking at a marketing chart.
 *
 * API_BASE points at your FastAPI backend. Change if you
 * deploy the backend somewhere other than localhost:8000.
 */

const API_BASE = "http://localhost:8000";

const CATEGORY_COLORS = {
  LOW:    { bg: "#0d2818", text: "#4ade80", border: "#16532e" },
  MEDIUM: { bg: "#2a2410", text: "#fbbf24", border: "#5c4a0a" },
  HIGH:   { bg: "#2e1a0a", text: "#fb923c", border: "#5c3210" },
  DANGER: { bg: "#2e0a0a", text: "#f87171", border: "#5c1010" },
};

function useLivePoll(endpoint, intervalMs = 5000) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const fetchData = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [endpoint]);

  useEffect(() => {
    fetchData();
    const id = setInterval(fetchData, intervalMs);
    return () => clearInterval(id);
  }, [fetchData, intervalMs]);

  return { data, error, loading };
}

function StatCard({ label, value, accent }) {
  return (
    <div
      style={{
        background: "#111317",
        border: "1px solid #23262d",
        borderRadius: 8,
        padding: "16px 20px",
        flex: 1,
        minWidth: 140,
      }}
    >
      <div style={{ fontSize: 11, color: "#6b7280", letterSpacing: "0.06em", textTransform: "uppercase", marginBottom: 6 }}>
        {label}
      </div>
      <div style={{ fontSize: 28, fontWeight: 600, color: accent || "#e5e7eb", fontFamily: "'JetBrains Mono', monospace" }}>
        {value}
      </div>
    </div>
  );
}

function CategoryBadge({ category }) {
  const c = CATEGORY_COLORS[category] || CATEGORY_COLORS.LOW;
  return (
    <span
      style={{
        background: c.bg,
        color: c.text,
        border: `1px solid ${c.border}`,
        borderRadius: 4,
        padding: "2px 8px",
        fontSize: 11,
        fontWeight: 600,
        fontFamily: "'JetBrains Mono', monospace",
        letterSpacing: "0.04em",
      }}
    >
      {category || "?"}
    </span>
  );
}

function QueryRow({ q }) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "90px 1fr 80px 70px",
        gap: 12,
        alignItems: "center",
        padding: "10px 14px",
        borderBottom: "1px solid #1a1d23",
        fontSize: 12,
      }}
    >
      <CategoryBadge category={q.cost_category} />
      <div
        style={{
          fontFamily: "'JetBrains Mono', monospace",
          color: "#9ca3af",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
        }}
        title={q.raw_sql}
      >
        {q.raw_sql}
      </div>
      <div style={{ color: "#e5e7eb", fontFamily: "'JetBrains Mono', monospace", textAlign: "right" }}>
        {q.total_cost != null ? q.total_cost.toFixed(1) : "—"}
      </div>
      <div style={{ color: "#6b7280", fontFamily: "'JetBrains Mono', monospace", textAlign: "right" }}>
        {q.exec_ms != null ? `${q.exec_ms.toFixed(1)}ms` : "—"}
      </div>
    </div>
  );
}

function SectionTitle({ children, eyebrow }) {
  return (
    <div style={{ marginBottom: 14 }}>
      {eyebrow && (
        <div style={{ fontSize: 10, color: "#4b5563", letterSpacing: "0.1em", textTransform: "uppercase", marginBottom: 3 }}>
          {eyebrow}
        </div>
      )}
      <div style={{ fontSize: 15, fontWeight: 600, color: "#e5e7eb" }}>{children}</div>
    </div>
  );
}

function Panel({ children, style }) {
  return (
    <div
      style={{
        background: "#0a0b0e",
        border: "1px solid #1f2228",
        borderRadius: 10,
        padding: 20,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function StatusDot({ ok }) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: ok ? "#4ade80" : "#f87171",
        marginRight: 6,
        boxShadow: ok ? "0 0 6px #4ade8088" : "0 0 6px #f8717188",
      }}
    />
  );
}

export default function QuerySentinelDashboard() {
  const { data: summary }      = useLivePoll("/stats/summary", 5000);
  const { data: recent, error: recentError } = useLivePoll("/queries/recent?limit=15", 4000);
  const { data: breakdown }    = useLivePoll("/stats/breakdown", 8000);
  const { data: recommendations } = useLivePoll("/recommendations/indexes", 15000);

  const backendUp = !recentError;

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#000000",
        color: "#e5e7eb",
        fontFamily: "Inter, -apple-system, sans-serif",
        padding: "28px 32px",
      }}
    >
      {/* ── Header ───────────────────────────────────────────── */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 28 }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: "-0.02em" }}>
            QuerySentinel
          </div>
          <div style={{ fontSize: 12, color: "#6b7280", marginTop: 2 }}>
            Autonomous Database Query Intelligence Platform
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", fontSize: 12, color: "#9ca3af" }}>
          <StatusDot ok={backendUp} />
          {backendUp ? "Live" : "Backend offline"}
        </div>
      </div>

      {/* ── Stat cards ───────────────────────────────────────── */}
      <div style={{ display: "flex", gap: 12, marginBottom: 24, flexWrap: "wrap" }}>
        <StatCard label="Total Queries"   value={summary?.total_queries ?? "—"} />
        <StatCard label="Danger Detected" value={summary?.danger_queries ?? "—"} accent="#f87171" />
        <StatCard label="Auto-Blocked"    value={summary?.blocked_queries ?? "—"} accent="#fb923c" />
        <StatCard label="Avg Exec Time"   value={summary ? `${summary.avg_exec_ms}ms` : "—"} />
        <StatCard label="Costliest Query" value={summary?.most_expensive ?? "—"} accent="#fbbf24" />
      </div>

      {/* ── Main grid ────────────────────────────────────────── */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 20 }}>
        {/* Live query feed */}
        <Panel>
          <SectionTitle eyebrow="Live feed">Recent intercepted queries</SectionTitle>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "90px 1fr 80px 70px",
              gap: 12,
              padding: "0 14px 8px 14px",
              fontSize: 10,
              color: "#4b5563",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              borderBottom: "1px solid #1a1d23",
            }}
          >
            <div>Category</div>
            <div>Query</div>
            <div style={{ textAlign: "right" }}>Cost</div>
            <div style={{ textAlign: "right" }}>Time</div>
          </div>
          <div style={{ maxHeight: 420, overflowY: "auto" }}>
            {recent && recent.length > 0 ? (
              recent.map((q, i) => <QueryRow key={i} q={q} />)
            ) : (
              <div style={{ padding: 24, textAlign: "center", color: "#4b5563", fontSize: 13 }}>
                No queries logged yet. Run the test app to generate traffic.
              </div>
            )}
          </div>
        </Panel>

        {/* Right column: breakdown + recommendations */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <Panel>
            <SectionTitle eyebrow="Distribution">Cost category breakdown</SectionTitle>
            {breakdown && breakdown.length > 0 ? (
              breakdown.map((b, i) => {
                const c = CATEGORY_COLORS[b.cost_category] || CATEGORY_COLORS.LOW;
                const maxCount = Math.max(...breakdown.map((x) => x.total_queries));
                const widthPct = (b.total_queries / maxCount) * 100;
                return (
                  <div key={i} style={{ marginBottom: 12 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
                      <span style={{ color: c.text, fontWeight: 600 }}>{b.cost_category}</span>
                      <span style={{ color: "#6b7280" }}>{b.total_queries}</span>
                    </div>
                    <div style={{ background: "#1a1d23", borderRadius: 4, height: 6, overflow: "hidden" }}>
                      <div
                        style={{
                          width: `${widthPct}%`,
                          height: "100%",
                          background: c.text,
                          borderRadius: 4,
                          transition: "width 0.4s ease",
                        }}
                      />
                    </div>
                  </div>
                );
              })
            ) : (
              <div style={{ color: "#4b5563", fontSize: 13 }}>No data yet</div>
            )}
          </Panel>

          <Panel>
            <SectionTitle eyebrow="Self-healing">Index recommendations</SectionTitle>
            {recommendations && recommendations.length > 0 ? (
              recommendations.slice(0, 4).map((r, i) => (
                <div
                  key={i}
                  style={{
                    background: "#0d1117",
                    border: "1px solid #1a1d23",
                    borderRadius: 6,
                    padding: "10px 12px",
                    marginBottom: 8,
                  }}
                >
                  <div style={{ fontSize: 12, color: "#e5e7eb", marginBottom: 3 }}>
                    <span style={{ color: "#60a5fa" }}>{r.table}</span>
                    <span style={{ color: "#4b5563" }}>.</span>
                    <span style={{ color: "#fbbf24" }}>{r.column}</span>
                  </div>
                  <div style={{ fontSize: 11, color: "#6b7280", marginBottom: 6 }}>{r.reason}</div>
                  <div
                    style={{
                      fontFamily: "'JetBrains Mono', monospace",
                      fontSize: 10,
                      color: "#4ade80",
                      background: "#000",
                      padding: "4px 8px",
                      borderRadius: 4,
                      overflowX: "auto",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {r.suggested_sql}
                  </div>
                </div>
              ))
            ) : (
              <div style={{ color: "#4b5563", fontSize: 13 }}>
                No recommendations yet — needs repeated sequential scans to detect a pattern.
              </div>
            )}
          </Panel>
        </div>
      </div>

      <div style={{ marginTop: 24, fontSize: 11, color: "#374151", textAlign: "center" }}>
        QuerySentinel — autonomous database query intelligence · polling every 5s
      </div>
    </div>
  );
}
