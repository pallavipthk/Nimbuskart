# Design Note: Hardening, Scaling, and Productionising Cost Janitor

## Multi-cloud reality

The current Janitor is a single Python file calling the AWS SDK directly. Adding
GCP in one quarter and Azure the next without rewrites requires an **abstraction
boundary** between the cloud-specific scanning code and the core logic (deduplication,
cost estimation, report schema, CLI).

```
janitor/
├── core/
│   ├── models.py        # Finding dataclass; the report.json schema lives here
│   ├── report.py        # build_report(), build_markdown() — cloud-agnostic
│   └── constants.py     # shared pricing tables, REQUIRED_TAGS
├── providers/
│   ├── base.py          # AbstractCloudProvider:
│   │                    #   scan_unattached_storage() -> list[Finding]
│   │                    #   scan_idle_compute()       -> list[Finding]
│   │                    #   scan_unassociated_ips()   -> list[Finding]
│   │                    #   scan_missing_tags()       -> list[Finding]
│   ├── aws/
│   │   ├── scanner.py   # implements AbstractCloudProvider via boto3
│   │   └── pricing.py   # EBS/EC2/EIP constants
│   ├── gcp/
│   │   ├── scanner.py   # implements AbstractCloudProvider via google-cloud-compute
│   │   └── pricing.py   # Persistent Disk / GCE pricing
│   └── azure/           # future — same pattern
├── janitor.py           # CLI: discovers providers from --cloud flag, fans out,
│                        # merges findings into one report
└── tests/
    ├── test_aws.py      # moto mocks
    └── test_gcp.py      # google-cloud-testutils or local emulator
```

`janitor.py --cloud aws,gcp --region us-east-1,us-central1` fans out to both
providers in parallel (threading or asyncio), merges findings into one report,
and emits a single `report.json`. Adding Azure is a new file under
`providers/azure/`; the CLI and core are untouched.

---

## Permissions

**Dry-run (read-only) — minimal IAM policy:**

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EC2ReadOnly",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeVolumes",
        "ec2:DescribeInstances",
        "ec2:DescribeAddresses",
        "ec2:DescribeTags",
        "ec2:DescribeInstanceStatus"
      ],
      "Resource": "*"
    },
    {
      "Sid": "STSGetIdentity",
      "Effect": "Allow",
      "Action": "sts:GetCallerIdentity",
      "Resource": "*"
    }
  ]
}
```

**Delete mode adds only the destructive actions needed:**

```
ec2:DeleteVolume        (resource: arn:aws:ec2:*:*:volume/*)
ec2:TerminateInstances  (resource: arn:aws:ec2:*:*:instance/*)
ec2:ReleaseAddress      (resource: *)
```

Critically, delete-mode permissions are **never** attached to the CI runner role.
Only a human-triggered Lambda (behind an SNS approval gate) or a break-glass IAM
role carries those permissions. The Janitor's default role is read-only.

---

## Safety net

**Failure mode 1 — temporarily detached data volume**  
An engineer detaches an EBS volume to take a snapshot or resize it, then is
interrupted before re-attaching. The Janitor runs that night and sees an
"unattached" volume that is days old — it deletes it, and the engineer returns
to find their data gone.

*Guardrail:* Before deleting any volume, check CloudTrail for a
`DetachVolume` event in the last 72 hours. If one exists, flag it for human
review instead of auto-deleting. Also require a second confirmation tag
(`JanitorApproved=true`) that an engineer must explicitly set after reviewing
the report. The Janitor only deletes volumes that are both unattached AND carry
that approval tag.

**Failure mode 2 — stopped instance with ephemeral local state**  
A data-pipeline instance is stopped temporarily while its EMR-style job results
sit in `/tmp` (not yet synced to S3). The Janitor terminates it after 14 days;
the job output is lost and the pipeline must re-run — a multi-hour blast radius.

*Guardrail:* Never auto-terminate instances that belong to an Auto Scaling Group
(check `DescribeAutoScalingInstances`), or that have a non-empty instance store.
Require the `Owner` tag to be present and send the owner a Slack DM with a
48-hour warning before termination. If the owner sets `Protected=true` within
that window, the termination is cancelled.

---

## Observability

Metrics published to **CloudWatch** (namespace `CostJanitor`) after every scan:

| Metric | Source | Alert threshold |
|--------|--------|-----------------|
| `OrphansFound` (count, by `ResourceType`) | Janitor scan | > 20 in any single scan → PagerDuty P3 |
| `EstimatedMonthlyWasteUSD` (gauge) | Janitor scan | > $500 → FinOps Slack channel |
| `ScanDurationSeconds` (timer) | `time.perf_counter()` around scan | > 300 s → engineering alert (API throttle or account sprawl) |
| `ResourcesDeleted` (count) | delete_finding() return values | > 10 per run → PagerDuty P2 (unexpected bulk deletion) |
| `ScanErrors` (count) | try/except around each scanner | > 0 → PagerDuty P2 (script regression or IAM revocation) |

Each metric carries dimensions `Account`, `Region`, and `Cloud` so the FinOps
team can filter by scope in a CloudWatch dashboard. A single Grafana panel pulls
from all three future cloud providers' namespaces.

---

## What I did not build

I scoped this implementation to the four orphan patterns specified plus CI/CD
wiring. Deliberately left out:

- **Multi-account scanning** — AWS Organizations `ListAccounts` + assumed roles
  per account would make the Janitor genuinely useful at enterprise scale, but
  would require a working cross-account IAM setup that can't be simulated cleanly
  in LocalStack free tier within the time budget.
- **AWS Cost Explorer integration** — replacing the hard-coded price table with
  real `GetCostAndUsage` API calls would improve accuracy, but adds an API
  dependency and cost per call.
- **RDS, Lambda, and EKS node-group scanning** — the four EC2/EBS/EIP/tag
  patterns cover NimbusKart's immediate problem; the module boundaries make
  adding new resource types straightforward.
- **Human-approval gate before deletion** — described in the Safety Net section
  above but not wired up; it requires SNS + Lambda infrastructure that is out
  of scope for a local demo.
- **Terraform state-drift detection** — comparing `terraform state list` against
  live AWS resources to find resources provisioned outside of IaC would be a
  high-value addition for a consulting client.
