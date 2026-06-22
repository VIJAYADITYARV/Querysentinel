# QuerySentinel — Backend Dockerfile
# Builds the FastAPI backend for ECS Fargate deployment
#
# Build:  docker build -t querysentinel-backend -f api/Dockerfile .
# Run:    docker run -p 8000:8000 --env-file .env querysentinel-backend

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies needed by psycopg2
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project (api, proxy, storage, ml, agent, schema)
COPY . .

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
