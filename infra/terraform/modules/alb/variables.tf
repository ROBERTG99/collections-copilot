variable "name" {
  description = "Name prefix for the load balancer resources."
  type        = string
}

variable "vpc_id" {
  description = "VPC id."
  type        = string
}

variable "vpc_cidr_block" {
  description = "VPC CIDR (the load balancer only talks to tasks inside it)."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnets in two availability zones."
  type        = list(string)
  validation {
    condition     = length(var.public_subnet_ids) >= 2
    error_message = "public_subnet_ids must list at least two subnets."
  }
}

variable "certificate_arn" {
  description = "ACM certificate for the HTTPS listener."
  type        = string
  validation {
    condition     = can(regex("^arn:aws:acm:[a-z0-9-]+:[0-9]{12}:certificate/.+$", var.certificate_arn))
    error_message = "certificate_arn must be an ACM certificate ARN."
  }
}

variable "targets" {
  description = "Target groups by service: container port, health path, and path patterns routed to it (empty for the default target)."
  type = map(object({
    port          = number
    health_path   = string
    path_patterns = optional(list(string), [])
    priority      = optional(number, 100)
  }))
}

variable "default_target" {
  description = "Target that receives every request no path rule matches."
  type        = string
}

variable "idle_timeout_seconds" {
  description = "Idle timeout; 120 s keeps SSE progress streams open."
  type        = number
  default     = 120
  validation {
    condition     = var.idle_timeout_seconds >= 60 && var.idle_timeout_seconds <= 4000
    error_message = "idle_timeout_seconds must be between 60 and 4000."
  }
}

variable "deletion_protection" {
  description = "Protect the load balancer from deletion."
  type        = bool
  default     = true
}

variable "access_logs_bucket" {
  description = "S3 bucket for access logs (null disables them; docs/security.md)."
  type        = string
  default     = null
}

variable "waf_rate_limit_per_5min" {
  description = "Requests per source IP per 5 minutes before the WAF blocks it."
  type        = number
  default     = 2000
  validation {
    condition     = var.waf_rate_limit_per_5min >= 100
    error_message = "waf_rate_limit_per_5min must be at least 100 (WAF minimum)."
  }
}

variable "kms_key_arn" {
  description = "KMS key for the WAF log group."
  type        = string
}

variable "webhook_path_prefix" {
  description = "Path prefix of the provider webhooks, exempt from the per-IP WAF rate limit."
  type        = string
  default     = "/api/v1/webhooks/twilio/"
  validation {
    condition     = startswith(var.webhook_path_prefix, "/") && endswith(var.webhook_path_prefix, "/")
    error_message = "webhook_path_prefix must start and end with a slash."
  }
}

variable "waf_webhook_rate_limit_per_5min" {
  description = "Requests per source IP per 5 minutes to the webhook path (Twilio shares addresses)."
  type        = number
  default     = 10000
  validation {
    condition     = var.waf_webhook_rate_limit_per_5min >= 100
    error_message = "waf_webhook_rate_limit_per_5min must be at least 100 (the WAF minimum)."
  }
}
