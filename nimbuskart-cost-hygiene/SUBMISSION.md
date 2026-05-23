# Submission — DevOps Engineer Assignment

**Candidate name:** _(your name)_
**Email:** _(your email)_
**Date submitted:** _(date)_
**Hours spent (approximate):** 8

## Deliverables checklist

- [x] Part A: Terraform code under /terraform applies cleanly on LocalStack
- [x] Part A: `terraform validate` and `terraform fmt -check` both pass
- [x] Part B: Janitor script runs in --dry-run mode and produces report.json
- [x] Part B: GitHub Actions workflow runs green on a fresh PR
- [x] Part B: --delete mode respects Protected=true tag
- [x] Part C: DESIGN.md is present and within 2 pages

## Walkthrough video

Link (Loom / YouTube unlisted / Google Drive): _(add before submitting)_
Length: max 5 minutes

## Sample report

Path to a sample report.json produced by your script: `samples/report.example.json`

## Known limitations

- The stopped-instance cost estimate uses 10 % of on-demand as a storage proxy rather than querying the actual root-volume size; it is conservative and noted in the report.
- LocalStack free tier does not enforce real AMI lookups; the `ami_id` variable accepts any non-empty string and is irrelevant to the assignment outcomes.
- Multi-account scanning is not implemented (see DESIGN.md and README Trade-offs).
- The walkthrough video link above must be filled in before submission.

## AI usage disclosure

See `README.md → AI usage disclosure` for the full breakdown of which tools were used, what the AI got wrong, and which section was written manually.
