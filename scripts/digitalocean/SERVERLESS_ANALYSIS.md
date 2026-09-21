# Serverless Cost Analysis for Trader Agent

## The Core Problem: Ollama (llama3) on Serverless

| Requirement | Serverless Reality |
|-------------|-------------------|
| **Model size** | llama3:8b = ~4.7 GB RAM minimum |
| **Cold start** | 10-30s to load model into memory |
| **Persistent memory** | Not available - model unloads after idle |
| **Max memory** | Lambda: 10 GB, Cloud Run: 32 GB, Functions: 4 GB |
| **Max timeout** | Lambda: 15 min, Cloud Run: 60 min |
| **GPU** | Lambda: No, Cloud Run: Yes (expensive), Modal/RunPod: Yes |

**Verdict**: Running Ollama on traditional serverless (Lambda, Cloud Functions) is **not practical** for a trading bot that needs sub-second response.

---

## Serverless Options Comparison

### 1. AWS Lambda + EFS (Model Storage)
| Component | Cost (est. 1500 invocations/mo) |
|-----------|--------------------------------|
| Lambda (10 GB, 30s avg) | $0.50 |
| EFS (10 GB storage) | $3.00 |
| API Gateway | $3.50 |
| **Total** | **~$7/mo** |
| **Problem** | Cold start 20-40s, EFS latency, no GPU, 15-min timeout |

### 2. Google Cloud Run (CPU only)
| Component | Cost |
|-----------|------|
| 2 vCPU, 4 GB, 100 req/day | ~$0.50/mo (free tier covers) |
| **With 4 GB always allocated** | **~$25/mo** (min instances=1) |
| **Problem** | No GPU, cold start if scaled to 0, 4 GB tight for llama3 |

### 3. Cloud Run with GPU (NVIDIA T4)
| Component | Cost |
|-----------|------|
| 1 GPU, 4 vCPU, 16 GB, min-instances=1 | **~$180/mo** |
| **Problem** | Expensive, overkill |

### 4. Modal / RunPod / Banana (GPU Serverless)
| Platform | Cost (T4, per hour) | Monthly (23h×22 days) |
|----------|---------------------|----------------------|
| **Modal** | $0.73/hr | **~$370/mo** |
| **RunPod** | $0.44/hr | **~$220/mo** |
| **Banana** | $0.80/hr | **~$400/mo** |
| **Problem** | Designed for batch inference, not always-on webhook |

### 5. Fly.io / Railway / Render (Container PaaS)
| Platform | Spec | Monthly |
|----------|------|---------|
| **Fly.io** | 2 vCPU, 4 GB, shared CPU | **~$20-30/mo** |
| **Railway** | 2 vCPU, 4 GB | **~$20/mo** |
| **Render** | 2 vCPU, 4 GB | **~$25/mo** |
| **Advantage** | Persistent containers, no cold start, easy deploy |
| **Problem** | Still pay for idle time |

---

## Hybrid Approach: Serverless Webhook + Separate Ollama

```
TradingView → API Gateway / Cloud Run (webhook only) → Queue → Ollama Server (always-on small VM)
```

| Component | Cost |
|-----------|------|
| **Webhook handler** (Cloud Run, scale to 0) | **$0-2/mo** |
| **Ollama VM** (DO s-1vcpu-2gb or similar) | **$12/mo** |
| **Queue** (SQS / Redis / NATS) | **$0-1/mo** |
| **Total** | **~$14/mo** |

**Tradeoff**: Added complexity, network latency, queue management.

---

## Realistic Comparison

| Architecture | Monthly Cost | Complexity | Latency | Reliability |
|--------------|--------------|------------|---------|-------------|
| **DO Droplet (s-2vcpu-4gb)** | **$24** | Low | ~50ms | High |
| **DO Droplet (s-1vcpu-2gb)** | **$12** | Low | ~100ms | Medium |
| **Fly.io / Railway** | $20-30 | Low | ~50ms | High |
| **Hybrid (Cloud Run + small VM)** | ~$14 | High | ~200ms | Medium |
| **Lambda + EFS** | ~$7 | High | ~5000ms (cold) | Low |
| **Modal/RunPod GPU** | $200-400 | Medium | ~100ms | Medium |

---

## Recommendation

### Don't use serverless for this workload because:

1. **Ollama needs persistent memory** - 4.7 GB minimum for llama3:8b
2. **Trading requires deterministic latency** - Cold starts = missed fills
3. **23h/day uptime** - You pay for idle anyway, negating serverless benefit
4. **Webhook volume is low** (10-50/day) - No scaling benefit

### Cheapest practical options:

| Option | Cost | Notes |
|--------|------|-------|
| **DO s-1vcpu-2gb** | **$12/mo** | Minimum for Ollama, tight but works |
| **Fly.io shared CPU 4GB** | ~$20/mo | Easy deploy, global, includes TLS |
| **Railway 4GB** | ~$20/mo | GitHub deploy, built-in PostgreSQL |
| **DO s-2vcpu-4gb** | **$24/mo** | **Best balance** - headroom for backtests, monitoring |

---

## If You Really Want Serverless

Use **Fly.io** or **Railway** - they're "serverless containers" with:
- No cold starts (always running)
- Per-second billing
- Built-in TLS, global Anycast
- Easy `fly deploy` / `railway up`

```bash
# Fly.io
fly launch --vm-size shared-cpu-2x --memory 4gb

# Railway
railway init && railway up
```

Both ~$20/mo, simpler than managing a raw VM.

---

## Bottom Line

**Stick with the DO droplet ($24/mo)**. The $12 savings on s-1vcpu-2gb isn't worth the risk of OOM kills during market hours. Serverless adds complexity without meaningful savings for this always-on, low-traffic, memory-heavy workload.