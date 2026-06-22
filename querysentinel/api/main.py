"""
QuerySentinel — FastAPI Backend (Week 4)
============================================
Exposes everything the React dashboard needs:
  - Live query log feed
  - Cost category breakdown stats
  - Agent decision history (accept/rewrite/escalate)
  - Index recommendations
  - Health check

Run: uvicorn api.main:app --reload --port 8000
Docs auto-generated at: http://localhost:8000/docs
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import psycopg2
import psycopg2.extras

from storage.writer import get_expensive_queries, get_category_breakdown
from agent.index_recommender import analyse_and_recommend, generate_index_sql

DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5433)),
    "dbname":   os.getenv("DB_NAME", "querysentinel"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "querysentinel"),
}

app = FastAPI(
    title="QuerySentinel API",
    description="Autonomous Database Query Intelligence Platform",
    version="1.0.0",
)

# CORS — allow the React dashboard (running on a different port) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this to your actual frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


# ── Response models ──────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    db_connected: bool
    total_queries_logged: int


class QueryLogItem(BaseModel):
    raw_sql:    str
    total_cost: Optional[float]
    cost_category: Optional[str]
    exec_ms:    Optional[float]
    danger_score: Optional[float]
    was_rewritten: Optional[bool] = False
    captured_at: str


class CategoryStats(BaseModel):
    cost_category: str
    total_queries: int
    avg_cost: float
    avg_exec_ms: float


class IndexRecommendation(BaseModel):
    table: str
    column: str
    reason: str
    occurrences: int
    avg_cost: float
    suggested_sql: str


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/", tags=["meta"])
def root():
    return {
        "service": "QuerySentinel API",
        "docs": "/docs",
        "endpoints": [
            "/health",
            "/queries/recent",
            "/queries/expensive",
            "/stats/breakdown",
            "/stats/summary",
            "/recommendations/indexes",
            "/agent/decisions",
        ],
    }


@app.get("/health", response_model=HealthResponse, tags=["meta"])
def health_check():
    """Verify DB connection and return total queries logged."""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM query_logs")
            total = cur.fetchone()[0]
        conn.close()
        return HealthResponse(status="ok", db_connected=True, total_queries_logged=total)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {e}")


@app.get("/queries/recent", tags=["queries"])
def recent_queries(limit: int = Query(default=50, le=200)):
    """Most recent intercepted queries — powers the live feed widget."""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    LEFT(raw_sql, 200) AS raw_sql,
                    total_cost,
                    cost_category,
                    exec_ms,
                    danger_score,
                    node_type,
                    has_seq_scan,
                    has_nested_loop,
                    captured_at
                FROM query_logs
                ORDER BY captured_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


@app.get("/queries/expensive", tags=["queries"])
def expensive_queries(limit: int = Query(default=10, le=50)):
    """Top N most expensive queries by cost — powers the leaderboard widget."""
    conn = get_db_connection()
    try:
        return get_expensive_queries(conn, limit=limit)
    finally:
        conn.close()


@app.get("/stats/breakdown", response_model=list[CategoryStats], tags=["stats"])
def category_breakdown():
    """Cost category distribution — powers the pie/bar chart."""
    conn = get_db_connection()
    try:
        rows = get_category_breakdown(conn)
        return [
            CategoryStats(
                cost_category=r["cost_category"],
                total_queries=r["total_queries"],
                avg_cost=float(r["avg_cost"] or 0),
                avg_exec_ms=float(r["avg_exec_ms"] or 0),
            )
            for r in rows
        ]
    finally:
        conn.close()


@app.get("/stats/summary", tags=["stats"])
def summary_stats():
    """High-level numbers for the dashboard header cards."""
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM query_logs")
            total = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*) FROM query_logs
                WHERE cost_category = 'DANGER'
            """)
            danger_count = cur.fetchone()[0]

            cur.execute("""
                SELECT COUNT(*) FROM query_logs
                WHERE node_type = 'BLOCKED'
            """)
            blocked_count = cur.fetchone()[0]

            cur.execute("""
                SELECT AVG(exec_ms) FROM query_logs WHERE exec_ms IS NOT NULL
            """)
            avg_ms = cur.fetchone()[0]

            cur.execute("""
                SELECT MAX(total_cost) FROM query_logs WHERE total_cost IS NOT NULL
            """)
            max_cost = cur.fetchone()[0]

        return {
            "total_queries":     total,
            "danger_queries":    danger_count,
            "blocked_queries":   blocked_count,
            "avg_exec_ms":       round(float(avg_ms or 0), 2),
            "most_expensive":    round(float(max_cost or 0), 1),
        }
    finally:
        conn.close()


@app.get("/recommendations/indexes", response_model=list[IndexRecommendation], tags=["self-healing"])
def index_recommendations():
    """Self-healing layer output — suggested indexes from query pattern analysis."""
    recs = analyse_and_recommend()
    return [
        IndexRecommendation(
            table=r["table"],
            column=r["column"],
            reason=r["reason"],
            occurrences=r["occurrences"],
            avg_cost=r["avg_cost"],
            suggested_sql=generate_index_sql(r),
        )
        for r in recs
    ]


@app.get("/agent/decisions", tags=["agent"])
def agent_decisions(limit: int = Query(default=20, le=100)):
    """
    Queries that went through the LLM rewrite agent —
    shows ACCEPT / ACCEPT_NO_CHANGE / ESCALATE decisions.
    Powers the 'agent activity log' widget.
    """
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    LEFT(raw_sql, 200) AS raw_sql,
                    total_cost,
                    node_type,
                    captured_at
                FROM query_logs
                WHERE cost_category = 'DANGER' OR node_type = 'BLOCKED'
                ORDER BY captured_at DESC
                LIMIT %s
            """, (limit,))
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.main:app", host="0.0.0.0", port=8000, reload=True)
