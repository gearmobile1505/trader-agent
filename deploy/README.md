# deploy/

Single document: **[MIGRATION_PLAN.md](MIGRATION_PLAN.md)**

## DigitalOcean Migration Plan — Option B (lightweight $6 droplet + cloud AI API)

Everything for the migration lives in that one file:

| Section | Contents |
|---|---|
| [1. Why Option B](MIGRATION_PLAN.md#1-why-option-b) | The reasoning, including why a 4 GB Ollama footprint is the wrong thing to pay for |
| [2. Target architecture](MIGRATION_PLAN.md#2-target-architecture) | ASCII diagram, what is removed and what is added |
| [3. Network and data flow](MIGRATION_PLAN.md#3-network-and-data-flow) | Topology, per-hop network flow, payload at every boundary, sequence and decision diagrams, failure-domain map |
| [4. Cost](MIGRATION_PLAN.md#4-cost) | $6-11/mo versus $24-29/mo, token estimate, spend-cap guardrail |
| [5. Audit](MIGRATION_PLAN.md#5-audit-of-the-current-system) | What is already correct in the repo, and the 11 gaps, ranked |
| [6. Code changes](MIGRATION_PLAN.md#6-code-changes) | Full `ai_decider.py`, the `main_cfd_5m.py` edits, `/health` |
| [7. Infrastructure changes](MIGRATION_PLAN.md#7-infrastructure-changes) | Terraform variables, `main.tf`, `user_data.sh`, firewall, requirements |
| [8. Migration procedure](MIGRATION_PLAN.md#8-migration-procedure) | Six phases with a verification gate on each |
| [9. Operations runbook](MIGRATION_PLAN.md#9-operations-runbook) | Health, monitoring, logs, scaling, env vars |
| [10. Rollback](MIGRATION_PLAN.md#10-rollback) | Per-phase, with time-to-recover |
| [11. Risk register](MIGRATION_PLAN.md#11-risk-register) | 11 risks with mitigations |
| [12. Troubleshooting](MIGRATION_PLAN.md#12-troubleshooting) | Symptom table and safety notes |

## Status

Proposal. Start at Phase 0 and do not proceed until its gate passes.

## Related, elsewhere in the repo

| Path | Role |
|---|---|
| `scripts/main_cfd_5m.py` | The service: `/webhook`, `/health`, `/status`, `/trailing-status`, `/symbols` |
| `scripts/monitor_health.py` | Cron health check, writes `scripts/health_monitor.log` |
| `terraform/digitalocean/` | IaC: droplet, firewall, DNS, cloud-init |
| `terraform/digitalocean/user_data.sh` | Bootstrap: swap, venv, systemd units, tunnel |
| `CLOUDFLARE_TUNNEL_SETUP.md` | Named tunnel procedure, used in Phase 4 |
| `SETUP_5M_LIVE.md` | Strategy and signal configuration |
| `scripts/alerts_log.jsonl` | Append-only decision log; the only record of why a trade was taken or skipped. Lives on the droplet — sync it before any replacement. |
