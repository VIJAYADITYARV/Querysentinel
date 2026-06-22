# QuerySentinel — AWS Deployment (Terraform)

This deploys QuerySentinel's backend on AWS using:
- **RDS PostgreSQL** (replaces your local Docker database)
- **ECS Fargate** (runs the FastAPI backend in a container)
- **ECR** (stores your Docker image)
- **S3** (stores ML model artifacts / MLflow runs)
- **CloudWatch** (logs)

## Prerequisites

```bash
# Install Terraform (if not already)
# https://developer.hashicorp.com/terraform/install

# Install AWS CLI and configure credentials
aws configure
# Enter your AWS Access Key ID, Secret Key, region (ap-south-1)
```

## Deploy

```bash
cd infra

terraform init

terraform plan \
  -var="db_password=YourSecurePassword123!" \
  -var="openai_api_key=sk-your-key-here"

terraform apply \
  -var="db_password=YourSecurePassword123!" \
  -var="openai_api_key=sk-your-key-here"
```

Type `yes` when prompted. Takes about 5-8 minutes (RDS provisioning is the slow part).

## Build and push your Docker image

After `terraform apply` completes, it prints an `ecr_repository_url`. Use it:

```bash
# Get login credentials for ECR
aws ecr get-login-password --region ap-south-1 | \
  docker login --username AWS --password-stdin <ecr_repository_url>

# Build your FastAPI backend image
docker build -t querysentinel-backend -f api/Dockerfile .

# Tag and push
docker tag querysentinel-backend:latest <ecr_repository_url>:latest
docker push <ecr_repository_url>:latest

# Force ECS to pick up the new image
aws ecs update-service \
  --cluster querysentinel-cluster \
  --service querysentinel-backend-svc \
  --force-new-deployment
```

## Migrate your schema to RDS

```bash
# Get the RDS endpoint from terraform output
terraform output rds_endpoint

# Run your init.sql against RDS instead of local Docker
psql -h <rds_endpoint> -U postgres -d querysentinel -f ../init.sql
```

## IMPORTANT — cost control

This costs roughly $25-40/month if left running continuously
(mostly RDS + Fargate). **For interview demos, this is fine to
spin up for a day and tear down after.**

```bash
# When done demoing — DESTROY everything to stop charges
terraform destroy \
  -var="db_password=YourSecurePassword123!" \
  -var="openai_api_key=sk-your-key-here"
```

## What to say in interviews

"I provisioned the entire AWS infrastructure — VPC, RDS,
ECS Fargate, ECR, IAM roles, CloudWatch logging — using
Terraform as Infrastructure as Code. The whole stack comes
up with one `terraform apply` command and tears down cleanly
with `terraform destroy`, so I only pay for compute when
actively demoing or testing."
