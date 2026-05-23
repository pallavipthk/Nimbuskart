# Walkthrough Video

> **Link:** _(to be added — see SUBMISSION.md)_  
> **Length:** < 5 minutes  

## Script outline

1. **Start LocalStack and apply Terraform**
   ```bash
   docker run --rm -d -p 4566:4566 --name localstack localstack/localstack
   cd terraform && tflocal init && tflocal apply -auto-approve
   tflocal output
   ```
   Point out the orphan volume ID in the outputs.

2. **Run the Cost Janitor and walk through a finding**
   ```bash
   cd ../janitor
   python janitor.py --dry-run --endpoint-url http://localhost:4566 --output-dir output
   cat output/report.json | python -m json.tool | head -40
   ```
   Walk through the `ebs_volume / unattached` finding — show the `safe_to_auto_delete`
   field, the cost estimate, and the missing-tags reason on the same volume.

3. **Design decision I'm proud of**
   The `_finding()` helper in `janitor.py` — all four scanners call it with named
   arguments so the report schema is enforced in one place. Adding a new field to
   every finding means touching exactly one function.

4. **One thing I would change**
   The stopped-instance cost estimate (10 % of on-demand) is a rough proxy.
   In production I'd pull the actual root-volume size from
   `describe_instance_attribute` and price it at the EBS gp3 rate instead.
