# QuerySentinel — Oracle Cloud Infrastructure (OCI) Deployment

QuerySentinel was designed with Oracle's Autonomous Database
philosophy in mind — this guide deploys it on OCI specifically,
which is the detail that makes Oracle recruiters take notice.

## Why OCI matters for this project's story

Oracle's flagship product, **Oracle Autonomous Database**, does
exactly what QuerySentinel's intelligence layer does — self-tuning,
self-optimising queries — but as a closed commercial product.
Deploying QuerySentinel on OCI (rather than only AWS) shows you
understand Oracle's own platform, not just a generic cloud target.

## Setup — OCI Always Free Tier

OCI's Always Free tier includes enough compute to run this
entire project at zero cost indefinitely — better than AWS for
a long-running student demo.

### 1. Create an OCI account and Compute instance

```bash
# Install OCI CLI
pip install oci-cli

# Configure with your OCI credentials
oci setup config
```

Through the OCI Console:
1. Create a **VM.Standard.A1.Flex** instance (Always Free — ARM-based, 4 OCPUs / 24GB RAM available free)
2. Choose **Ubuntu 22.04** as the image
3. Open ports 8000 (API), 5432 (Postgres), 80/443 if adding a frontend
4. Note the public IP address

### 2. Install Docker on the OCI instance

```bash
ssh ubuntu@<your-oci-public-ip>

sudo apt update
sudo apt install -y docker.io docker-compose
sudo usermod -aG docker $USER
# log out and back in for group change to apply
```

### 3. Deploy QuerySentinel

```bash
git clone https://github.com/YOUR_USERNAME/querysentinel
cd querysentinel

# Use your existing docker-compose.yml — works unchanged on OCI
docker-compose up -d

pip install -r requirements.txt

# Run the proxy + API
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

### 4. (Optional) Use OCI Autonomous Database instead of Docker Postgres

For maximum "speaks Oracle's language" impact, swap your local
Postgres for an actual **OCI Autonomous Database** instance —
free tier includes one. This means QuerySentinel is literally
monitoring an Oracle Autonomous Database, which is the most
on-the-nose demo possible for an Oracle interview.

```bash
# Through OCI Console: Autonomous Database > Create
# Choose "Always Free" configuration
# Download the wallet (connection credentials)

# Install Oracle's Python driver
pip install oracledb

# Update your DB_CONFIG in proxy/main.py to use oracledb
# instead of psycopg2 for this specific deployment target
```

> Note: switching the underlying driver from psycopg2 (Postgres)
> to oracledb (Oracle DB) requires adapting `explainer.py` since
> Oracle's EXPLAIN PLAN syntax differs from Postgres's
> `EXPLAIN (ANALYZE, FORMAT JSON)`. This is intentionally left
> as a stretch goal — even mentioning you're aware of this
> distinction is a strong signal in an Oracle interview.

## What to say in interviews

"I deployed QuerySentinel on Oracle Cloud Infrastructure using
their Always Free tier — an ARM-based compute instance running
the full stack. I'm aware that adapting the EXPLAIN-parsing layer
to work natively against Oracle Autonomous Database would require
swapping from psycopg2 to Oracle's python driver and rewriting
the plan-parsing logic for Oracle's EXPLAIN PLAN format instead
of Postgres's JSON output — which is the next step if I were to
productionise this for Oracle's own database engine."

This sentence alone demonstrates you understand the difference
between "ran on Oracle's cloud" and "built for Oracle's database
engine" — most students conflate the two.
