# Cloud Provider Cost Comparison: AWS vs DigitalOcean
## For Trader Agent Webhook Hosting (5-min scalping system)

---

## Requirements
- **Workload**: Python FastAPI webhook server + Ollama (llama3) + nginx
- **Traffic**: ~10-50 webhooks/day (TradingView alerts)
- **Uptime**: 23h/day (market hours), 5 days/week
- **Memory**: ~2-3GB (Ollama + Python + nginx)
- **CPU**: Bursty (webhook processing + AI inference)

---

## DigitalOcean Pricing (Monthly, On-Demand)

| Droplet Size | vCPU | RAM | SSD | Monthly | Hourly | Best For |
|--------------|------|-----|-----|---------|--------|----------|
| **s-1vcpu-1gb** | 1 | 1 GB | 25 GB | **$6** | $0.009 | Too small for Ollama |
| **s-1vcpu-2gb** | 1 | 2 GB | 50 GB | **$12** | $0.018 | Minimum viable |
| **s-2vcpu-2gb** | 2 | 2 GB | 60 GB | **$18** | $0.027 | **Recommended** |
| **s-2vcpu-4gb** | 2 | 4 GB | 80 GB | **$24** | $0.036 | Comfortable headroom |
| **s-4vcpu-8gb** | 4 | 8 GB | 160 GB | **$48** | $0.071 | Overkill |

**Recommended: s-2vcpu-4gb ($24/mo)** - 2 vCPU, 4GB RAM handles Ollama + webhook comfortably

### Additional DO Costs
- **Floating IP**: $4/mo (if needed for static IP)
- **Backups**: +20% droplet cost ($4.80/mo for s-2vcpu-4gb)
- **Monitoring**: Free
- **Firewall**: Free
- **VPC**: Free
- **DNS**: Free
- **Data transfer**: 1 TB included, then $0.01/GB

---

## AWS Pricing (Monthly, On-Demand, us-east-1)

| Instance Type | vCPU | RAM | Storage (EBS) | Monthly* | Best For |
|---------------|------|-----|---------------|----------|----------|
| **t4g.nano** | 2 | 0.5 GB | 8 GB gp3 | **$3.72** | Too small |
| **t4g.micro** | 2 | 1 GB | 20 GB gp3 | **$7.44** | Too small for Ollama |
| **t4g.small** | 2 | 2 GB | 30 GB gp3 | **$14.88** | Minimum viable |
| **t4g.medium** | 2 | 4 GB | 40 GB gp3 | **$29.76** | **Recommended** |
| **t3.medium** | 2 | 4 GB | 40 GB gp3 | **$33.58** | Intel alternative |
| **t4g.large** | 2 | 8 GB | 60 GB gp3 | **$59.52** | Overkill |

\* Includes 20 GB gp3 EBS @ $0.08/GB/mo = $1.60/mo

### Additional AWS Costs
- **Elastic IP**: Free (attached), $3.65/mo (unattached)
- **Data transfer**: First 100 GB/mo free, then $0.09/GB
- **CloudWatch**: Basic free, detailed $0.30/metric/mo
- **Route 53**: $0.50/hosted zone/mo + $0.40/million queries
- **Systems Manager**: Free (for session manager)

---

## Spot/Preemptible Comparison

| Provider | Spot/Preemptible | Savings | Risk |
|----------|------------------|---------|------|
| **DO** | No native spot (but can use [Spot by NetApp](https://spot.io/)) | N/A | N/A |
| **AWS** | **t4g.medium Spot** | ~**$8-12/mo** (60-70% off) | **High** - can be terminated with 2-min notice |

**Not recommended for trading** - termination during market hours = missed alerts = lost trades.

---

## Reserved / Savings Plans (1-year)

| Provider | Commitment | Effective Monthly | Savings |
|----------|------------|-------------------|---------|
| **DO** | No reserved instances | N/A | N/A |
| **AWS** | 1yr No Upfront Savings Plan | **~$18/mo** (t4g.medium) | ~40% |
| **AWS** | 1yr All Upfront | **~$14/mo** | ~50% |

---

## Total Cost of Ownership (Monthly)

### DigitalOcean (Recommended: s-2vcpu-4gb)
| Item | Cost |
|------|------|
| Droplet (s-2vcpu-4gb) | $24.00 |
| Backups (optional) | $4.80 |
| Floating IP (optional) | $4.00 |
| **Total (basic)** | **$24.00** |
| **Total (with backups)** | **$28.80** |

### AWS (Recommended: t4g.medium)
| Item | Cost |
|------|------|
| EC2 (t4g.medium on-demand) | $29.76 |
| EBS (40 GB gp3) | $3.20 |
| Elastic IP (attached) | $0.00 |
| Route 53 (optional) | $0.50 |
| Data transfer (est. 10 GB) | $0.00 |
| **Total (basic)** | **$33.46** |
| **Total (1yr Savings Plan)** | **~$18.00** |

---

## Verdict

| Factor | DigitalOcean | AWS |
|--------|--------------|-----|
| **Simplicity** | ⭐⭐⭐⭐⭐ Single dashboard, predictable pricing | ⭐⭐ Complex billing, many services |
| **On-Demand Cost** | **$24/mo** (winner) | $33/mo |
| **Reserved Cost** | N/A | **$18/mo** (1yr commitment) |
| **Static IP** | $4/mo (Floating IP) | Free (Elastic IP attached) |
| **DNS** | Free | $0.50/zone/mo |
| **SSL/ACME** | Manual (certbot) | ACM free (but needs ALB ~$16/mo) |
| **Time to deploy** | ~3 min | ~5-10 min |
| **Ollama performance** | Good (AMD EPYC) | Good (Graviton ARM) |

---

## Recommendation

### For your use case: **DigitalOcean s-2vcpu-4gb ($24/mo)**

**Reasons:**
1. **Simpler** - Single droplet, no VPC/ALB/IAM complexity
2. **Cheaper on-demand** - $24 vs $33/mo
3. **Predictable billing** - No surprise data transfer charges
4. **Floating IP** - $4/mo gives you a static IP for TradingView
5. **ARM vs x86** - DO uses AMD EPYC (x86), better Ollama compatibility
6. **Less lock-in** - Easier to migrate later

### If you want cheapest long-term: **AWS t4g.medium 1yr Savings Plan ($18/mo)**
- Requires 1-year commitment
- More complex setup (VPC, SG, IAM, ALB for SSL)
- Graviton ARM - verify Ollama/llama3 works (it does, but slower than x86)

---

## Quick Deploy Commands

### DigitalOcean
```bash
cd terraform/digitalocean
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values
terraform init
terraform plan
terraform apply
```

### Get webhook URL after deploy
```bash
terraform output webhook_url
# https://webhook.yourdomain.com/webhook  (with domain)
# http://<droplet_ip>:8000/webhook       (without domain)
```

---

## Migration Checklist

- [ ] Push main_cfd_5m.py to git repo
- [ ] Update TradingView alerts to new webhook URL
- [ ] Test webhook end-to-end from TradingView
- [ ] Verify SSL cert (Let's Encrypt via certbot)
- [ ] Set up monitoring/alerting (DO monitoring + UptimeRobot free)
- [ ] Configure daily P&L report cron
- [ ] Document rollback procedure