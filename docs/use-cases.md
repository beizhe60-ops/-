# Use Cases

## Community operations

Monitor selected Telegram groups for phrases such as payment trouble, login issue, or refund request, then route matched messages into a queue for human review.

## Research and OSINT

Track public channels or groups for incident keywords, suspicious infrastructure references, or fraud indicators, then archive the matched messages for later analysis.

## Internal team alerts

Use Telegram as one more signal source for incident coordination, customer escalation, or partner communication. Rules can flag key phrases and store them in a searchable local database.

## Recommended operating model

- Start with `record_only`
- Validate what the rule engine catches
- Review false positives and cooldown settings
- Enable replies only when the workflow has clear ownership and compliance review
