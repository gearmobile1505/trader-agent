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

resource "digitalocean_droplet" "trading_bot" {
  image              = "ubuntu-24-04-x64"
  name               = "${var.project_name}-${var.environment}-2"
  region             = var.region
  size               = var.droplet_size
  ssh_keys           = [var.ssh_fingerprint]
  user_data          = templatefile("${path.module}/user_data.sh", {
    tradelocker_email = var.tradelocker_email
    tradelocker_password = var.tradelocker_password
    tradelocker_server = var.tradelocker_server
    ollama_model = var.ollama_model
    risk_per_trade = var.risk_per_trade
    daily_loss_limit = var.daily_loss_limit
    max_concurrent_positions = var.max_concurrent_positions
  })
  monitoring         = true
  ipv6               = true
  tags               = ["trading", "webhook", var.environment]

  lifecycle {
    create_before_destroy = true
    ignore_changes = [
      ssh_keys,
      user_data,
    ]
  }
  
}

resource "digitalocean_firewall" "trading_firewall" {
  name = "${var.project_name}-firewall-v2"

  droplet_ids = [digitalocean_droplet.trading_bot.id]

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

resource "digitalocean_domain" "trading_domain" {
  count = var.domain_name != "" ? 1 : 0
  name  = var.domain_name
  ip_address = digitalocean_droplet.trading_bot.ipv4_address
}

resource "digitalocean_record" "api_subdomain" {
  count = var.domain_name != "" ? 1 : 0
  domain = digitalocean_domain.trading_domain[0].name
  type   = "A"
  name   = "api"
  value  = digitalocean_droplet.trading_bot.ipv4_address
  ttl    = 300
}

resource "digitalocean_record" "webhook_subdomain" {
  count = var.domain_name != "" ? 1 : 0
  domain = digitalocean_domain.trading_domain[0].name
  type   = "A"
  name   = "webhook"
  value  = digitalocean_droplet.trading_bot.ipv4_address
  ttl    = 300
}