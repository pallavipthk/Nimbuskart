#!/usr/bin/env python3
"""
Cost Janitor — detect and optionally clean up orphaned AWS resources.

Orphan patterns detected:
  1. EBS volumes in 'available' state (not attached to any instance).
  2. EC2 instances in 'stopped' state for more than N days (default 14).
  3. Elastic IPs not associated with any instance or network interface.
  4. Resources missing one or more required tags (Project, Environment,
     Owner, ManagedBy).

Usage:
  # Dry-run (default) — report only, exit 1 if orphans found:
  python janitor.py [--dry-run] [--endpoint-url URL] [--region REGION]

  # Delete mode — removes safe orphans, skips Protected=true:
  python janitor.py --delete [--endpoint-url URL] [--region REGION]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from constants import (
    DEFAULT_EBS_COST_PER_GB,
    DEFAULT_EC2_COST_PER_HOUR,
    EBS_COST_PER_GB_MONTH,
    EC2_COST_PER_HOUR,
    ELASTIC_IP_COST_PER_MONTH,
    HOURS_PER_MONTH,
    PROTECTED_TAG_KEY,
    PROTECTED_TAG_VALUE,
    REQUIRED_TAGS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("janitor")

# ── Tag helpers ───────────────────────────────────────────────────────────────


def get_tag(tags: list[dict], key: str) -> Optional[str]:
    """Return the value for *key* from an AWS tags list, or None."""
    for tag in tags or []:
        if tag.get("Key") == key:
            return tag.get("Value")
    return None


def tags_to_dict(tags: list[dict]) -> dict[str, str]:
    return {t["Key"]: t["Value"] for t in (tags or [])}


def is_protected(tags: list[dict]) -> bool:
    return get_tag(tags, PROTECTED_TAG_KEY) == PROTECTED_TAG_VALUE


def missing_required_tags(tags: list[dict]) -> list[str]:
    d = tags_to_dict(tags)
    return [t for t in REQUIRED_TAGS if not d.get(t)]


def required_tag_snapshot(tags: list[dict]) -> dict[str, Optional[str]]:
    """Return a dict of all required tags and their current values (None if absent)."""
    d = tags_to_dict(tags)
    return {t: d.get(t) for t in REQUIRED_TAGS}


# ── Scanners ──────────────────────────────────────────────────────────────────


def scan_unattached_volumes(ec2) -> list[dict]:
    """EBS volumes in 'available' state — not attached to any instance."""
    findings: list[dict] = []
    paginator = ec2.get_paginator("describe_volumes")

    for page in paginator.paginate(
        Filters=[{"Name": "status", "Values": ["available"]}]
    ):
        for vol in page["Volumes"]:
            vol_id = vol["VolumeId"]
            size_gb = vol.get("Size", 0)
            vol_type = vol.get("VolumeType", "gp3")
            tags = vol.get("Tags", [])

            create_time: datetime = vol.get("CreateTime", datetime.now(timezone.utc))
            if isinstance(create_time, str):
                create_time = datetime.fromisoformat(create_time.replace("Z", "+00:00"))
            if create_time.tzinfo is None:
                create_time = create_time.replace(tzinfo=timezone.utc)

            age_days = (datetime.now(timezone.utc) - create_time).days
            monthly_cost = EBS_COST_PER_GB_MONTH.get(vol_type, DEFAULT_EBS_COST_PER_GB) * size_gb

            findings.append(
                _finding(
                    resource_id=vol_id,
                    resource_type="ebs_volume",
                    reason="unattached",
                    age_days=age_days,
                    monthly_cost=monthly_cost,
                    tags=tags,
                    suggested_action="delete",
                    # Only safe to auto-delete if not protected; data volumes need
                    # extra caution — see DESIGN.md § Safety net.
                    safe_to_auto_delete=not is_protected(tags),
                )
            )

    return findings


def scan_stopped_instances(ec2, stopped_days_threshold: int = 14) -> list[dict]:
    """EC2 instances stopped for longer than *stopped_days_threshold* days."""
    findings: list[dict] = []
    paginator = ec2.get_paginator("describe_instances")

    for page in paginator.paginate(
        Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]
    ):
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                instance_id = inst["InstanceId"]
                instance_type = inst.get("InstanceType", "t3.micro")
                tags = inst.get("Tags", [])

                # AWS encodes the stop timestamp in StateTransitionReason:
                # "User initiated (2024-01-15 10:30:00 GMT)"
                state_reason: str = inst.get("StateTransitionReason", "")
                age_days = _parse_stop_age_days(state_reason, stopped_days_threshold)

                if age_days < stopped_days_threshold:
                    continue

                # Stopped instances still incur EBS root-volume storage cost.
                # We report ~10 % of on-demand as a conservative storage proxy.
                cost_per_hour = EC2_COST_PER_HOUR.get(instance_type, DEFAULT_EC2_COST_PER_HOUR)
                monthly_cost = cost_per_hour * HOURS_PER_MONTH * 0.10

                findings.append(
                    _finding(
                        resource_id=instance_id,
                        resource_type="ec2_instance",
                        reason=f"stopped_for_{age_days}_days",
                        age_days=age_days,
                        monthly_cost=monthly_cost,
                        tags=tags,
                        suggested_action="terminate",
                        safe_to_auto_delete=not is_protected(tags),
                    )
                )

    return findings


def scan_unassociated_eips(ec2) -> list[dict]:
    """Elastic IPs not associated with any instance or network interface."""
    findings: list[dict] = []
    response = ec2.describe_addresses()

    for addr in response.get("Addresses", []):
        if addr.get("InstanceId") or addr.get("AssociationId"):
            continue  # Still in use

        allocation_id = addr.get("AllocationId") or addr.get("PublicIp", "unknown")
        tags = addr.get("Tags", [])

        findings.append(
            _finding(
                resource_id=allocation_id,
                resource_type="elastic_ip",
                reason="unassociated",
                age_days=0,  # AWS does not expose EIP allocation timestamp
                monthly_cost=ELASTIC_IP_COST_PER_MONTH,
                tags=tags,
                suggested_action="release",
                safe_to_auto_delete=not is_protected(tags),
                _extra={"public_ip": addr.get("PublicIp", "")},
            )
        )

    return findings


def scan_missing_tags(ec2) -> list[dict]:
    """Resources missing one or more required tags."""
    findings: list[dict] = []
    seen: set[str] = set()

    # EC2 instances
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                if inst.get("State", {}).get("Name") == "terminated":
                    continue
                instance_id = inst["InstanceId"]
                tags = inst.get("Tags", [])
                missing = missing_required_tags(tags)
                if missing and instance_id not in seen:
                    seen.add(instance_id)
                    findings.append(
                        _finding(
                            resource_id=instance_id,
                            resource_type="ec2_instance",
                            reason=f"missing_tags:{','.join(missing)}",
                            age_days=0,
                            monthly_cost=0.0,
                            tags=tags,
                            suggested_action="tag",
                            safe_to_auto_delete=False,
                        )
                    )

    # EBS volumes
    paginator = ec2.get_paginator("describe_volumes")
    for page in paginator.paginate():
        for vol in page["Volumes"]:
            volume_id = vol["VolumeId"]
            tags = vol.get("Tags", [])
            missing = missing_required_tags(tags)
            if missing and volume_id not in seen:
                seen.add(volume_id)
                findings.append(
                    _finding(
                        resource_id=volume_id,
                        resource_type="ebs_volume",
                        reason=f"missing_tags:{','.join(missing)}",
                        age_days=0,
                        monthly_cost=0.0,
                        tags=tags,
                        suggested_action="tag",
                        safe_to_auto_delete=False,
                    )
                )

    return findings


# ── Delete actions ────────────────────────────────────────────────────────────


def delete_finding(ec2, finding: dict) -> bool:
    """
    Attempt to act on a finding.

    Returns True if the resource was removed.
    Silently skips Protected=true resources.
    """
    raw_tags = finding.get("_raw_tags", [])
    if is_protected(raw_tags):
        log.info("SKIP  %s %s — Protected=true", finding["resource_type"], finding["resource_id"])
        return False

    r_type = finding["resource_type"]
    r_id = finding["resource_id"]
    action = finding["suggested_action"]

    try:
        if r_type == "ebs_volume" and action == "delete":
            ec2.delete_volume(VolumeId=r_id)
            log.info("DEL   EBS volume %s", r_id)
            return True

        if r_type == "ec2_instance" and action == "terminate":
            ec2.terminate_instances(InstanceIds=[r_id])
            log.info("TERM  EC2 instance %s", r_id)
            return True

        if r_type == "elastic_ip" and action == "release":
            ec2.release_address(AllocationId=r_id)
            log.info("REL   Elastic IP %s", r_id)
            return True

        log.warning("No delete handler for %s / %s — skipping", r_type, action)
        return False

    except ClientError as exc:
        log.error("ERROR deleting %s %s: %s", r_type, r_id, exc)
        return False


# ── Report builders ───────────────────────────────────────────────────────────


def build_report(findings: list[dict], account_id: str, region: str) -> dict:
    """Assemble the canonical report.json structure (schema v1)."""
    # Deduplicate by (resource_id, reason) — tag scanner may overlap with
    # other scanners for the same resource.
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for f in findings:
        key = (f["resource_id"], f["reason"])
        if key not in seen:
            seen.add(key)
            # Strip internal _ keys before output
            clean = {k: v for k, v in f.items() if not k.startswith("_")}
            unique.append(clean)

    total_waste = round(sum(f["estimated_monthly_cost_usd"] for f in unique), 2)

    return {
        "scan_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "account_id": account_id,
        "region": region,
        "summary": {
            "total_orphans": len(unique),
            "estimated_monthly_waste_usd": total_waste,
        },
        "findings": unique,
    }


def build_markdown(report: dict) -> str:
    """Render a human-readable Markdown summary of the report."""
    lines = [
        "## 🧹 Cost Janitor Report",
        "",
        f"**Scan time:** `{report['scan_timestamp']}`  ",
        f"**Account:** `{report['account_id']}`  ",
        f"**Region:** `{report['region']}`  ",
        "",
        "### Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total orphans | **{report['summary']['total_orphans']}** |",
        f"| Est. monthly waste | **${report['summary']['estimated_monthly_waste_usd']:.2f}** |",
        "",
    ]

    if not report["findings"]:
        lines.append("✅ **No orphaned resources found.**")
        return "\n".join(lines)

    lines += [
        "### Findings",
        "",
        "| Resource ID | Type | Reason | Age (days) | Est. Cost/mo | Safe to Delete |",
        "|-------------|------|--------|-----------|-------------|----------------|",
    ]
    for f in report["findings"]:
        safe_icon = "✅" if f["safe_to_auto_delete"] else "⚠️ No"
        lines.append(
            f"| `{f['resource_id']}` "
            f"| {f['resource_type']} "
            f"| {f['reason']} "
            f"| {f['age_days']} "
            f"| ${f['estimated_monthly_cost_usd']:.2f} "
            f"| {safe_icon} |"
        )

    lines += [
        "",
        "> **Note:** Resources tagged `Protected=true` are never auto-deleted.",
        "> Run the Janitor with `--delete` to remove resources marked *Safe to Delete*.",
    ]
    return "\n".join(lines)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _finding(
    *,
    resource_id: str,
    resource_type: str,
    reason: str,
    age_days: int,
    monthly_cost: float,
    tags: list[dict],
    suggested_action: str,
    safe_to_auto_delete: bool,
    _extra: dict | None = None,
) -> dict:
    """Construct a canonical finding dict."""
    entry: dict = {
        "resource_id": resource_id,
        "resource_type": resource_type,
        "reason": reason,
        "age_days": age_days,
        "estimated_monthly_cost_usd": round(monthly_cost, 2),
        "tags": required_tag_snapshot(tags),
        "suggested_action": suggested_action,
        "safe_to_auto_delete": safe_to_auto_delete,
        # Internal keys (stripped before JSON output)
        "_raw_tags": tags,
    }
    if _extra:
        for k, v in _extra.items():
            entry[f"_{k}"] = v
    return entry


def _parse_stop_age_days(state_reason: str, fallback: int) -> int:
    """
    Parse stop age from EC2 StateTransitionReason.

    Format: "User initiated (2024-01-15 10:30:00 GMT)"
    Returns *fallback* if the timestamp cannot be parsed (conservatively flags
    the instance so a human can review it).
    """
    try:
        if "(" not in state_reason or ")" not in state_reason:
            return fallback
        time_str = state_reason.split("(")[1].rstrip(")")
        stop_time = datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M:%S %Z")
        stop_time = stop_time.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - stop_time).days
    except (ValueError, IndexError):
        return fallback


def _get_account_id(ec2) -> str:
    """Retrieve the AWS account ID via STS (falls back to LocalStack default)."""
    try:
        sts = boto3.client(
            "sts",
            region_name=ec2.meta.region_name,
            endpoint_url=ec2.meta.endpoint_url,
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        return sts.get_caller_identity()["Account"]
    except Exception:
        return "000000000000"


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cost Janitor — detect and optionally clean up orphaned AWS resources.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Report orphans without deleting them. Exits 1 if any are found. (default)",
    )
    mode.add_argument(
        "--delete",
        action="store_true",
        default=False,
        help="Delete detected orphans. Skips resources tagged Protected=true.",
    )
    p.add_argument("--region", default="us-east-1", help="AWS region to scan.")
    p.add_argument(
        "--endpoint-url",
        default=None,
        metavar="URL",
        help="Custom AWS endpoint (e.g. http://localhost:4566 for LocalStack).",
    )
    p.add_argument(
        "--stopped-days",
        type=int,
        default=14,
        metavar="N",
        help="Flag EC2 instances stopped for at least N days. (default: 14)",
    )
    p.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Directory to write report.json and report.md. (default: .)",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    delete_mode: bool = args.delete
    dry_run: bool = not delete_mode

    client_kwargs: dict = {
        "region_name": args.region,
        "aws_access_key_id": "test",
        "aws_secret_access_key": "test",
    }
    if args.endpoint_url:
        client_kwargs["endpoint_url"] = args.endpoint_url

    ec2 = boto3.client("ec2", **client_kwargs)
    account_id = _get_account_id(ec2)

    log.info(
        "Starting Cost Janitor — mode=%s  region=%s  account=%s",
        "DELETE" if delete_mode else "DRY-RUN",
        args.region,
        account_id,
    )

    findings: list[dict] = []
    findings.extend(scan_unattached_volumes(ec2))
    findings.extend(scan_stopped_instances(ec2, args.stopped_days))
    findings.extend(scan_unassociated_eips(ec2))
    findings.extend(scan_missing_tags(ec2))

    log.info("Scan complete — %d raw finding(s) before deduplication.", len(findings))

    if delete_mode:
        for f in findings:
            delete_finding(ec2, f)

    report = build_report(findings, account_id, args.region)
    markdown = build_markdown(report)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    (output_dir / "report.json").write_text(json.dumps(report, indent=2))
    (output_dir / "report.md").write_text(markdown)

    log.info(
        "Report written → %s  (%d orphan(s), $%.2f/month waste)",
        output_dir,
        report["summary"]["total_orphans"],
        report["summary"]["estimated_monthly_waste_usd"],
    )

    if dry_run and report["summary"]["total_orphans"] > 0:
        log.warning(
            "%d orphan(s) found. Review report.json then re-run with --delete to clean up.",
            report["summary"]["total_orphans"],
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
