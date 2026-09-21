output "droplet_ip" {
  description = "Public IPv4 address of the main droplet"
  value       = digitalocean_droplet.trading_bot.ipv4_address
}

output "droplet_ipv6" {
  description = "Public IPv6 address of the droplet"
  value       = digitalocean_droplet.trading_bot.ipv6_address
}

output "droplet_name" {
  description = "Name of the droplet"
  value       = digitalocean_droplet.trading_bot.name
}

output "ssh_command" {
  description = "SSH command to connect to the droplet"
  value       = "ssh root@${digitalocean_droplet.trading_bot.ipv4_address}"
}

output "webhook_url" {
  description = "Webhook URL for TradingView alerts"
  value       = var.domain_name != "" ? "https://webhook.${var.domain_name}/webhook" : "http://${digitalocean_droplet.trading_bot.ipv4_address}:8000/webhook"
}

output "api_url" {
  description = "API base URL"
  value       = var.domain_name != "" ? "https://api.${var.domain_name}" : "http://${digitalocean_droplet.trading_bot.ipv4_address}:8000"
}

output "firewall_name" {
  description = "Name of the firewall"
  value       = digitalocean_firewall.trading_firewall.name
}

output "estimated_monthly_cost_usd" {
  description = "Estimated monthly cost in USD"
  value = (var.droplet_size == "s-1vcpu-1gb" ? 6
    : var.droplet_size == "s-1vcpu-2gb" ? 12
    : var.droplet_size == "s-2vcpu-2gb" ? 18
    : var.droplet_size == "s-2vcpu-4gb" ? 24
    : var.droplet_size == "s-4vcpu-8gb" ? 48
  : 24)
}
