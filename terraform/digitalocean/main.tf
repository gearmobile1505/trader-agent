terraform {
  required_version = ">= 1.5.0"
  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.0"
    }
  }
}

provider "digitalocean" {
  token = var.do_token
}

# ---------------------------------------------------------------------------
# Existing live server (143.198.7.200) — TRACKED READ-ONLY.
#
# This droplet is running live trades behind a Quick Tunnel. It is imported
# into state so Terraform knows about it (and will not create a duplicate or
# orphan it on destroy), but every attribute is ignored so a plan or apply can
# never modify it. Migrate traffic to `new_architecture` before changing it.
# Import: terraform import digitalocean_droplet.existing_prod 602517171
# ---------------------------------------------------------------------------
resource "digitalocean_droplet" "existing_prod" {
  image      = "ubuntu-24-04-x64"
  name       = "trader-agent-prod-3"
  region     = "nyc3"
  size       = "s-2vcpu-4gb"
  ssh_keys   = [var.ssh_fingerprint]
  monitoring = true
  ipv6       = true
  tags       = ["trading", "webhook", "prod", "legacy-ollama"]

  lifecycle {
    ignore_changes = all
  }
}

# ---------------------------------------------------------------------------
# New architecture (138.197.8.50) — MANAGED.
#
# Option B: 1 GB droplet, no local Ollama. The AI risk review is delegated to
# DeepSeek. Previously hosted OWASP Juice Shop; the container was removed
# manually, so this droplet is imported rather than created.
# Import: terraform import digitalocean_droplet.new_architecture 603469248
# ---------------------------------------------------------------------------
resource "digitalocean_droplet" "new_architecture" {
  # These must match the imported droplet exactly. `image` is a ForceNew
  # attribute on the DO provider: if it differs, Terraform destroys the
  # droplet and creates a new one instead of reusing this server. The droplet
  # was built from the docker-20-04 image (Ubuntu 22.04.5 underneath) and was
  # never opted into DO monitoring.
  image      = "docker-20-04"
  monitoring = false

  name       = "trader-agent-v2"
  region     = var.region
  size       = var.droplet_size
  ssh_keys   = [var.ssh_fingerprint]
  ipv6       = true
  tags       = ["trading", "webhook", var.environment, "option-b", "deepseek"]

  user_data = templatefile("${path.module}/user_data.sh", {
    tradelocker_email        = var.tradelocker_email
    tradelocker_password     = var.tradelocker_password
    tradelocker_server       = var.tradelocker_server
    ollama_model             = var.ollama_model
    ai_provider              = var.ai_provider
    ai_fail_open             = var.ai_fail_open
    cloud_api_url            = var.cloud_api_url
    cloud_api_key            = var.cloud_api_key
    cloud_api_model          = var.cloud_api_model
    risk_per_trade           = var.risk_per_trade
    daily_loss_limit         = var.daily_loss_limit
    max_concurrent_positions = var.max_concurrent_positions
  })

  lifecycle {
    # Imported droplet: never let a size change silently replace it.
    create_before_destroy = true
    ignore_changes        = [ssh_keys, user_data]
  }
}


resource "digitalocean_firewall" "trading_firewall" {
  name = "${var.project_name}-firewall-v2"

  droplet_ids = [digitalocean_droplet.new_architecture.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = var.ssh_ip
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "80"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "443"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "443"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "7844"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "7844"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "53"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }
}

# ---------------------------------------------------------------------------
# DNS is managed by Cloudflare, not DigitalOcean.
#
# cello1505.com is delegated to Cloudflare (zelda/simon.ns.cloudflare.com) and
# its records are proxied. The named tunnel creates the webhook CNAME via
# `cloudflared tunnel route dns`, so there is deliberately no
# digitalocean_domain / digitalocean_record resource here. Adding one would
# fight Cloudflare for zone ownership and fail on apply.
# ---------------------------------------------------------------------------
