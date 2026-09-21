variable "do_token" {
  description = "DigitalOcean Personal Access Token"
  type        = string
  sensitive   = true
}

variable "ssh_fingerprint" {
  description = "SSH key fingerprint for droplet access"
  type        = string
}

variable "ssh_ip" {
  description = "Your Mac's public IPv4 (run: curl -4 -s https://ifconfig.me)"
  type        = list(string)
  default     = ["174.209.193.129", "73.106.218.36"]
}

variable "project_name" {
  description = "Project name for resource naming"
  type        = string
  default     = "trader-agent"
}

variable "environment" {
  description = "Environment name (dev, staging, prod)"
  type        = string
  default     = "prod"
}

variable "region" {
  description = "DigitalOcean region"
  type        = string
  default     = "nyc3"
  validation {
    condition     = contains(["nyc1", "nyc3", "sfo2", "sfo3", "ams3", "sgp1", "lon1", "fra1", "tor1", "blr1"], var.region)
    error_message = "Invalid region. Choose from: nyc1, nyc3, sfo2, sfo3, ams3, sgp1, lon1, fra1, tor1, blr1"
  }
}

variable "droplet_size" {
  description = "Droplet size slug"
  type        = string
  default     = "s-2vcpu-4gb"
  validation {
    condition     = contains(["s-1vcpu-1gb", "s-1vcpu-2gb", "s-2vcpu-2gb", "s-2vcpu-4gb", "s-4vcpu-8gb"], var.droplet_size)
    error_message = "Invalid size. Choose from: s-1vcpu-1gb, s-1vcpu-2gb, s-2vcpu-2gb, s-2vcpu-4gb, s-4vcpu-8gb"
  }
}

variable "domain_name" {
  description = "Domain name for DNS records (optional)"
  type        = string
  default     = ""
}

variable "tradelocker_email" {
  description = "TradeLocker account email"
  type        = string
  sensitive   = true
}

variable "tradelocker_password" {
  description = "TradeLocker account password"
  type        = string
  sensitive   = true
}

variable "tradelocker_server" {
  description = "TradeLocker server (e.g., GATESFX)"
  type        = string
  default     = "GATESFX"
}

variable "ollama_model" {
  description = "Ollama model to use"
  type        = string
  default     = "phi3:mini"
}

variable "risk_per_trade" {
  description = "Risk per trade in USD"
  type        = number
  default     = 200
}

variable "daily_loss_limit" {
  description = "Daily loss limit in USD"
  type        = number
  default     = 400
}

variable "max_concurrent_positions" {
  description = "Maximum concurrent positions"
  type        = number
  default     = 3
}