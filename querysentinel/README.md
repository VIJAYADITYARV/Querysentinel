# QuerySentinel

**Autonomous Database Query Intelligence Platform**

QuerySentinel sits between your application and PostgreSQL, intercepts every SQL query, predicts its execution cost using ML, and autonomously rewrites slow or dangerous queries using an LLM agent — before they damage production.

> Open-source implementation of the core intelligence layer in Oracle Autonomous Database.

---

## Architecture

```
┌─────────────┐     SQL query      ┌──────────────────────┐     optimised SQL    ┌──────────────┐
│  Flask App  │ ─────────────────► │  QuerySentinel Proxy │ ───────────────────► │  PostgreSQL  │
└─────────────┘                    └──────────────────────┘                       └──────────────┘
                                            │
                              ┌─────────────┼─────────────┐
                              ▼             ▼             ▼
                       EXPLAIN ANALYZE   ML Cost      LLM Rewrite
                       (parse plan)    Predictor       Agent
                              │             │             │
                              └─────────────┴─────────────┘
                                            │
                                            ▼
                                    TimescaleDB
                                   (query_logs)
                                            │
                                            ▼
                                   Grafana Dashboard
```

---

## Modules

| Module | Status | Description |
|--------|--------|-------------|
| Proxy + Interceptor | ✅ Week 1 | Intercepts all SQL, runs EXPLAIN ANALYZE |
| TimescaleDB Storage | ✅ Week 1 | Persists query logs as time-series data |
| ML Cost Predictor | 🔄 Week 2 | GNN trained on query execution plans |
| LLM Rewrite Agent | 🔄 Week 3 | LangGraph agent rewrites slow queries |
| Self-healing layer | 🔄 Week 3 | Index recommender, partition suggester |
| Dashboard + OCI | 🔄 Week 4 | React UI + Oracle OCI + AWS deployment |

---

## Run Locally (Week 1)

**Prerequisites:** Docker Desktop, Python 3.11+

```bash
# 1. Clone repo
git clone https://github.com/YOUR_USERNAME/querysentinel
cd querysentinel

# 2. Start PostgreSQL + TimescaleDB + pgAdmin
docker-compose up -d

# 3. Install dependencies
pip install -r requirements.txt

# 4. Run the demo (proxy intercepts 5 queries)
python proxy/main.py

# 5. Start the Flask test app (separate terminal)
python testapp/app.py

# 6. Generate 50 queries and verify logging (day 5)
python test_traffic.py
```

**pgAdmin:** http://localhost:8080
Email: `admin@querysentinel.com` | Password: `admin`

**Verify query logs:**
```sql
SELECT LEFT(raw_sql, 80) AS query, total_cost, node_type, exec_ms
FROM query_logs
ORDER BY total_cost DESC
LIMIT 10;
```

---

## The 5 Query Types (Test App)

| Route | Query Type | Expected Cost |
|-------|-----------|---------------|
| `/users` | Simple SELECT + LIMIT | LOW |
| `/orders` | 3-table JOIN | MEDIUM |
| `/reports` | GROUP BY aggregation | MEDIUM-HIGH |
| `/search` | Full table scan (LIKE) | HIGH |
| `/summary` | Correlated subquery | DANGER |

---

## Tech Stack

**Week 1:** Python, PostgreSQL, TimescaleDB, psycopg2, sqlparse, Docker
**Week 2:** PyTorch, GNN, MLflow, scikit-learn
**Week 3:** LangGraph, OpenAI API, FastAPI
**Week 4:** React, Oracle OCI, AWS RDS, Terraform, GitHub Actions

---

## Built by

Vijay — Final Year CSE, Amrita Vishwa Vidyapeetham (2027)
