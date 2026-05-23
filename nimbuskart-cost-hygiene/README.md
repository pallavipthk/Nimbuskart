# NimbusKart Cost Hygiene

> **DevOps Assignment — Code & Conscience**  
> Multi-Cloud Cost Hygiene & Automation Challenge

## Overview

This repository contains the full solution to the NimbusKart cost-hygiene
assignment. It provisions a small AWS staging environment in LocalStack (Part A),
runs an automated "Cost Janitor" that detects orphaned and untagged resources
(Part B), and includes a GitHub Actions workflow that enforces cost hygiene on
every pull request. A two-page design note (Part C) covers how the system would
be hardened and extended to GCP and Azure.

---

## How to run locally

### Prerequisites

| Tool | Version |
|------|---------|
| Docker | any recent |
| Python | 3.10+ |
| Terraform | 1.5+ |

```bash
# 1. Clone
git clone https://github.com/<you>/nimbuskart-cost-hygiene.git
cd nimbuskart-cost-hygiene

# 2. Start LocalStack
docker run --rm -d -p 4566:4566 --name localstack localstack/localstack:3.8
# Wait ~10 s for LocalStack to initialise, then:
curl -s http://localhost:4566/_localstack/health | python -m json.tool

# 3. Install tooling
pip install terraform-local awscli-local
pip install -r janitor/requirements.txt

# 4. Apply Terraform
cd terraform
tflocal init
tflocal apply -auto-approve
tflocal output          # note the orphan_volume_id
cd ..

# 5. Run the Cost Janitor (dry-run — exits 1 if orphans found)
python janitor/janitor.py \
  --dry-run \
  --endpoint-url http://localhost:4566 \
  --output-dir janitor/output

cat janitor/output/report.json
cat janitor/output/report.md

# 6. (Optional) Delete safe orphans
python janitor/janitor.py \
  --delete \
  --endpoint-url http://localhost:4566 \
  --output-dir janitor/output

# 7. Run unit tests (uses moto — no LocalStack needed)
cd janitor && pytest tests/ -v && cd ..

# 8. Tear down
cd terraform && tflocal destroy -auto-approve && cd ..
docker stop localstack
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    GitHub Actions CI                     │
│                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────┐  │
│  │  LocalStack  │    │  Terraform   │    │  Cost     │  │
│  │  (service    │◄───│  (tflocal    │    │  Janitor  │  │
│  │  container)  │    │  apply)      │    │  (Python) │  │
│  └──────┬───────┘    └──────────────┘    └─────┬─────┘  │
│         │  EC2 / S3 / EBS / EIP APIs           │        │
│         └──────────────────────────────────────┘        │
│                          │                               │
│              ┌───────────▼──────────┐                   │
│              │  report.json         │  ──► artifact      │
│              │  report.md           │  ──► PR comment    │
│              └──────────────────────┘                   │
└─────────────────────────────────────────────────────────┘

Terraform module layout
───────────────────────
terraform/
├── provider.tf          LocalStack provider config
├── main.tf              Root: SG, EC2, S3, orphan EBS
├── variables.tf / outputs.tf
└── modules/network/     VPC, subnets, IGW, route tables

Janitor internal layout
────────────────────────
janitor/
├── constants.py         Pricing tables (cited sources)
├── janitor.py           4 scanners + delete + CLI
└── tests/               moto-based unit tests
```

---

## Decisions & deviations

- **SSH CIDR default changed from `0.0.0.0/0` to `10.0.0.0/8`** — the spec says "flag this"; opening port 22 to the world is a security misconfiguration that would immediately be flagged in any cloud security audit. The variable still exists so callers can override it explicitly.
- **Orphan EBS volume intentionally omits `Project`, `Environment`, `Owner` tags** — this violates the "every resource must be tagged" rule, but is required to give the Janitor's tag-compliance scanner a known target in CI. In a real account this fixture would live in a separate throwaway stack.
- **Added Internet Gateway and route tables** — the spec lists VPC and subnets but a public subnet without an IGW and route table cannot route traffic; this is a spec gap, not a deviation.
- **`provider.tf` uses inline endpoint config rather than relying on `tflocal` exclusively** — `tflocal` is used locally, but CI also needs to set `TF_VAR_localstack_endpoint`; having the variable in `variables.tf` makes the endpoint explicit and auditable in both contexts.
- **`aws_s3_bucket_versioning` and `aws_s3_bucket_lifecycle_configuration` are separate resources** — the monolithic `aws_s3_bucket` `versioning` and `lifecycle_rule` blocks were deprecated in AWS provider 4.x. Using the standalone resources avoids a future breaking change.
- **S3 `abort_incomplete_multipart_upload` added to lifecycle rule** — the spec only mentions non-current version expiry; incomplete multipart uploads are a common hidden cost vector and cost nothing to guard against.
- **Stopped-instance cost is estimated at 10 % of on-demand** — stopped instances don't pay for compute but do pay for root-volume storage. Without calling `describe_instance_attribute` for every instance, we use 10 % of on-demand as a conservative proxy. The real cost is typically lower; this is noted in the report.

---

## Trade-offs

With one more week I would:

1. **Replace the static price table with AWS Price List API calls** — the hard-coded constants will drift; the Price List API gives real-time per-region, per-type prices.
2. **Add multi-account support** — use `organizations:ListAccounts` and `sts:AssumeRole` so the Janitor fans out across every AWS account in the org.
3. **Wire up the GCP provider** — the abstract `AbstractCloudProvider` interface is designed for this; adding `providers/gcp/scanner.py` is ~200 lines of google-cloud-compute SDK calls.
4. **Add an approval gate before deletion** — publish findings to SNS, require a human to confirm (or set `JanitorApproved=true`), and only then invoke the delete Lambda.
5. **Publish CloudWatch metrics** — the five metrics in DESIGN.md, pushed via `cloudwatch.put_metric_data()` at the end of every scan.

---

## AI usage disclosure

- **Claude (Anthropic)** was used to generate the initial scaffolding for this entire repository — directory structure, Terraform module skeleton, janitor script outline, and GitHub Actions workflow boilerplate.
- **One thing AI got wrong:** the initial GitHub Actions health-check for the LocalStack service container used `wget` instead of `curl`, which is not installed in the default `localstack/localstack` image. This caused the health check to fail silently; I caught it by reading the Docker health-check output carefully and switched to `curl -sf`.
- **One section written without AI:** the `_parse_stop_age_days()` function was written manually. The initial AI suggestion parsed the `StateTransitionReason` string with a regex that didn't account for the trailing `)` without a space, causing a `ValueError` on real AWS output. I read the AWS EC2 API documentation for `StateTransitionReason`, reproduced the exact format, and wrote the parser from scratch using `split("(")[1].rstrip(")")` which handles both `"User initiated (2024-01-15 10:30:00 GMT)"` and edge cases like missing timestamps.
