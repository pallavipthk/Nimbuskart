"""
Pricing constants for AWS resource cost estimation.

All prices are US East (N. Virginia) on-demand rates.

Sources:
  EBS:        https://aws.amazon.com/ebs/pricing/          (retrieved 2026-01)
  EC2:        https://aws.amazon.com/ec2/pricing/on-demand/ (retrieved 2026-01)
  Elastic IP: https://aws.amazon.com/vpc/pricing/           (retrieved 2026-01)
"""

# EBS cost per GB per month (gp3 is the current default volume type).
EBS_COST_PER_GB_MONTH: dict[str, float] = {
    "gp2": 0.10,
    "gp3": 0.08,
    "io1": 0.125,
    "io2": 0.125,
    "st1": 0.045,
    "sc1": 0.025,
    "standard": 0.05,
}

# EC2 on-demand cost per hour.
EC2_COST_PER_HOUR: dict[str, float] = {
    "t3.micro": 0.0104,
    "t3.small": 0.0208,
    "t3.medium": 0.0416,
    "t3.large": 0.0832,
    "t2.micro": 0.0116,
    "t2.small": 0.0230,
    "t2.medium": 0.0464,
    "m5.large": 0.0960,
    "m5.xlarge": 0.1920,
    "c5.large": 0.0850,
}

# Elastic IPs billed per hour when NOT associated with a running instance.
# Source: $0.005/hour = $3.60/month (730 h/month).
ELASTIC_IP_COST_PER_HOUR: float = 0.005

# Approximate hours in a calendar month.
HOURS_PER_MONTH: int = 730

ELASTIC_IP_COST_PER_MONTH: float = round(ELASTIC_IP_COST_PER_HOUR * HOURS_PER_MONTH, 2)

# Default fallbacks when the actual volume/instance type is unknown.
DEFAULT_EBS_COST_PER_GB: float = EBS_COST_PER_GB_MONTH["gp3"]
DEFAULT_EC2_COST_PER_HOUR: float = EC2_COST_PER_HOUR["t3.micro"]

# Tags that every resource must carry for cost attribution.
REQUIRED_TAGS: list[str] = ["Project", "Environment", "Owner", "ManagedBy"]

# Resources tagged Protected=true are never auto-deleted in --delete mode.
PROTECTED_TAG_KEY: str = "Protected"
PROTECTED_TAG_VALUE: str = "true"
