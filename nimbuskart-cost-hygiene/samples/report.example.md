## 🧹 Cost Janitor Report

**Scan time:** `2026-01-15T10:00:00Z`
**Account:** `000000000000`
**Region:** `us-east-1`

### Summary

| Metric | Value |
|--------|-------|
| Total orphans | **4** |
| Est. monthly waste | **$15.42** |

### Findings

| Resource ID | Type | Reason | Age (days) | Est. Cost/mo | Safe to Delete |
|-------------|------|--------|-----------|-------------|----------------|
| `vol-0a1b2c3d4e5f67890` | ebs_volume | unattached | 21 | $1.60 | ✅ |
| `i-0deadbeef1234567` | ec2_instance | stopped_for_22_days | 22 | $7.60 | ✅ |
| `eipalloc-0f1e2d3c4b5a6789` | elastic_ip | unassociated | 0 | $3.65 | ✅ |
| `vol-0a1b2c3d4e5f67890` | ebs_volume | missing_tags:Project,Environment,Owner | 0 | $0.00 | ⚠️ No |

> **Note:** Resources tagged `Protected=true` are never auto-deleted.
> Run the Janitor with `--delete` to remove resources marked *Safe to Delete*.
