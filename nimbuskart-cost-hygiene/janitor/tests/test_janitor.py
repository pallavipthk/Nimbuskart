"""
Unit tests for Cost Janitor using moto to mock the AWS APIs.

Run:
    cd janitor && pytest tests/ -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

# Make janitor importable from tests/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from janitor import (  # noqa: E402
    _finding,
    build_markdown,
    build_report,
    delete_finding,
    get_tag,
    is_protected,
    missing_required_tags,
    required_tag_snapshot,
    scan_missing_tags,
    scan_unassociated_eips,
    scan_unattached_volumes,
    scan_stopped_instances,
)
from constants import REQUIRED_TAGS  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

REGION = "us-east-1"


@pytest.fixture
def ec2():
    with mock_aws():
        yield boto3.client("ec2", region_name=REGION)


# ── Tag helpers ───────────────────────────────────────────────────────────────


def test_get_tag_found():
    tags = [{"Key": "Project", "Value": "nimbuskart"}]
    assert get_tag(tags, "Project") == "nimbuskart"


def test_get_tag_missing():
    assert get_tag([], "Project") is None
    assert get_tag(None, "Project") is None


def test_is_protected_true():
    assert is_protected([{"Key": "Protected", "Value": "true"}]) is True


def test_is_protected_false():
    assert is_protected([{"Key": "Protected", "Value": "false"}]) is False
    assert is_protected([]) is False


def test_missing_required_tags_all_present():
    full_tags = [{"Key": t, "Value": "v"} for t in REQUIRED_TAGS]
    assert missing_required_tags(full_tags) == []


def test_missing_required_tags_some_absent():
    tags = [{"Key": "Project", "Value": "nk"}]
    missing = missing_required_tags(tags)
    assert "Environment" in missing
    assert "Owner" in missing
    assert "ManagedBy" in missing
    assert "Project" not in missing


# ── EBS volume scanner ────────────────────────────────────────────────────────


@mock_aws
def test_scan_unattached_volumes_detects_available():
    ec2 = boto3.client("ec2", region_name=REGION)
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=20, VolumeType="gp3")

    findings = scan_unattached_volumes(ec2)

    assert len(findings) == 1
    f = findings[0]
    assert f["resource_type"] == "ebs_volume"
    assert f["reason"] == "unattached"
    assert f["estimated_monthly_cost_usd"] == round(20 * 0.08, 2)  # gp3 rate
    assert f["suggested_action"] == "delete"


@mock_aws
def test_scan_unattached_volumes_empty_when_none():
    ec2 = boto3.client("ec2", region_name=REGION)
    # No volumes created
    assert scan_unattached_volumes(ec2) == []


@mock_aws
def test_scan_unattached_volumes_protected_not_safe():
    ec2 = boto3.client("ec2", region_name=REGION)
    vol = ec2.create_volume(
        AvailabilityZone=f"{REGION}a",
        Size=10,
        VolumeType="gp3",
        TagSpecifications=[
            {
                "ResourceType": "volume",
                "Tags": [{"Key": "Protected", "Value": "true"}],
            }
        ],
    )

    findings = scan_unattached_volumes(ec2)
    assert len(findings) == 1
    assert findings[0]["safe_to_auto_delete"] is False


# ── Elastic IP scanner ────────────────────────────────────────────────────────


@mock_aws
def test_scan_unassociated_eips_detects_free_eip():
    ec2 = boto3.client("ec2", region_name=REGION)
    ec2.allocate_address(Domain="vpc")

    findings = scan_unassociated_eips(ec2)

    assert len(findings) == 1
    assert findings[0]["resource_type"] == "elastic_ip"
    assert findings[0]["reason"] == "unassociated"
    assert findings[0]["estimated_monthly_cost_usd"] > 0


@mock_aws
def test_scan_unassociated_eips_empty_when_none():
    ec2 = boto3.client("ec2", region_name=REGION)
    assert scan_unassociated_eips(ec2) == []


# ── Missing-tags scanner ──────────────────────────────────────────────────────


@mock_aws
def test_scan_missing_tags_flags_untagged_volume():
    ec2 = boto3.client("ec2", region_name=REGION)
    ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=5, VolumeType="gp3")

    findings = scan_missing_tags(ec2)

    assert any(f["resource_type"] == "ebs_volume" for f in findings)
    vol_finding = next(f for f in findings if f["resource_type"] == "ebs_volume")
    assert "missing_tags" in vol_finding["reason"]
    assert vol_finding["safe_to_auto_delete"] is False


@mock_aws
def test_scan_missing_tags_skip_fully_tagged_volume():
    ec2 = boto3.client("ec2", region_name=REGION)
    ec2.create_volume(
        AvailabilityZone=f"{REGION}a",
        Size=5,
        VolumeType="gp3",
        TagSpecifications=[
            {
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "Project", "Value": "nk"},
                    {"Key": "Environment", "Value": "staging"},
                    {"Key": "Owner", "Value": "platform"},
                    {"Key": "ManagedBy", "Value": "terraform"},
                ],
            }
        ],
    )

    findings = scan_missing_tags(ec2)
    vol_findings = [f for f in findings if f["resource_type"] == "ebs_volume"]
    assert vol_findings == []


# ── Delete action ─────────────────────────────────────────────────────────────


@mock_aws
def test_delete_finding_skips_protected():
    ec2 = boto3.client("ec2", region_name=REGION)
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=5)
    vol_id = vol["VolumeId"]

    finding = _finding(
        resource_id=vol_id,
        resource_type="ebs_volume",
        reason="unattached",
        age_days=10,
        monthly_cost=1.0,
        tags=[{"Key": "Protected", "Value": "true"}],
        suggested_action="delete",
        safe_to_auto_delete=False,
    )

    result = delete_finding(ec2, finding)
    assert result is False

    # Volume should still exist
    vols = ec2.describe_volumes(VolumeIds=[vol_id])["Volumes"]
    assert len(vols) == 1


@mock_aws
def test_delete_finding_removes_unprotected_volume():
    ec2 = boto3.client("ec2", region_name=REGION)
    vol = ec2.create_volume(AvailabilityZone=f"{REGION}a", Size=5)
    vol_id = vol["VolumeId"]

    finding = _finding(
        resource_id=vol_id,
        resource_type="ebs_volume",
        reason="unattached",
        age_days=10,
        monthly_cost=1.0,
        tags=[],
        suggested_action="delete",
        safe_to_auto_delete=True,
    )

    result = delete_finding(ec2, finding)
    assert result is True


# ── Report & Markdown builders ────────────────────────────────────────────────


def test_build_report_schema():
    findings = [
        _finding(
            resource_id="vol-abc123",
            resource_type="ebs_volume",
            reason="unattached",
            age_days=21,
            monthly_cost=8.00,
            tags=[],
            suggested_action="delete",
            safe_to_auto_delete=True,
        )
    ]
    report = build_report(findings, "123456789012", REGION)

    assert "scan_timestamp" in report
    assert report["account_id"] == "123456789012"
    assert report["region"] == REGION
    assert report["summary"]["total_orphans"] == 1
    assert report["summary"]["estimated_monthly_waste_usd"] == 8.00
    assert len(report["findings"]) == 1

    # Internal keys must not leak into output
    assert not any(k.startswith("_") for k in report["findings"][0])


def test_build_report_deduplicates():
    f = _finding(
        resource_id="vol-abc123",
        resource_type="ebs_volume",
        reason="unattached",
        age_days=5,
        monthly_cost=4.0,
        tags=[],
        suggested_action="delete",
        safe_to_auto_delete=True,
    )
    report = build_report([f, f], "000000000000", REGION)
    assert report["summary"]["total_orphans"] == 1


def test_build_markdown_no_findings():
    report = {
        "scan_timestamp": "2026-01-15T10:00:00Z",
        "account_id": "000000000000",
        "region": REGION,
        "summary": {"total_orphans": 0, "estimated_monthly_waste_usd": 0.0},
        "findings": [],
    }
    md = build_markdown(report)
    assert "No orphaned resources found" in md


def test_build_markdown_with_findings():
    report = {
        "scan_timestamp": "2026-01-15T10:00:00Z",
        "account_id": "000000000000",
        "region": REGION,
        "summary": {"total_orphans": 1, "estimated_monthly_waste_usd": 8.00},
        "findings": [
            {
                "resource_id": "vol-abc123",
                "resource_type": "ebs_volume",
                "reason": "unattached",
                "age_days": 21,
                "estimated_monthly_cost_usd": 8.00,
                "tags": {t: None for t in REQUIRED_TAGS},
                "suggested_action": "delete",
                "safe_to_auto_delete": True,
            }
        ],
    }
    md = build_markdown(report)
    assert "vol-abc123" in md
    assert "$8.00" in md
    assert "Cost Janitor Report" in md


def test_report_json_is_valid():
    """Smoke test: build_report output is serialisable to JSON."""
    findings = [
        _finding(
            resource_id="eipalloc-xyz",
            resource_type="elastic_ip",
            reason="unassociated",
            age_days=0,
            monthly_cost=3.65,
            tags=[],
            suggested_action="release",
            safe_to_auto_delete=True,
        )
    ]
    report = build_report(findings, "000000000000", REGION)
    serialised = json.dumps(report)
    parsed = json.loads(serialised)
    assert parsed["summary"]["total_orphans"] == 1
