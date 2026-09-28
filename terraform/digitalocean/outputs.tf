output "droplet_ip" {
  description = "Public IPv4 of the new Option B droplet"
  value       = digitalocean_droplet.new_architecture.ipv4_address
}

output "droplet_name" {
  description = "Name of the new Option B droplet"
  value       = digitalocean_droplet.new_architecture.name
}

output "new_ssh_command" {
  description = "SSH into the new Option B droplet"
  value       = "ssh root@${digitalocean_droplet.new_architecture.ipv4_address}"
}

output "existing_prod_ip" {
  description = "Public IPv4 of the existing live server (read-only in Terraform)"
  value       = digitalocean_droplet.existing_prod.ipv4_address
}

output "existing_prod_ssh_command" {
  description = "SSH into the existing live server"
  value       = "ssh root@${digitalocean_droplet.existing_prod.ipv4_address}"
}

output "public_health_url" {
  description = "Public health endpoint once the named tunnel is live"
  value       = "https://${var.domain_name}/health"
}

output "public_webhook_url" {
  description = "TradingView webhook URL once the named tunnel is live"
  value       = "https://${var.domain_name}/webhook"
}

output "ai_backend" {
  description = "Which AI layer is configured (no secrets)"
  value       = "${var.ai_provider}:${var.cloud_api_model}"
}

output "estimated_monthly_cost_usd" {
  description = "Estimated monthly cost in USD"
  value = (
    # New Option B droplet
    (var.droplet_size == "s-1vcpu-1gb" ? 6
      : var.droplet_size == "s-1vcpu-2gb" ? 12
      : var.droplet_size == "s-2vcpu-2gb" ? 18
      : var.droplet_size == "s-2vcpu-4gb" ? 24
    : 48)
    # Existing live droplet, still running until cutover
    + 24
  )
}
