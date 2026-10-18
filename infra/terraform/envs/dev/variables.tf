variable "aws_region" {
  description = "AWS region for every resource."
  type        = string
  default     = "us-east-1"
  validation {
    condition     = can(regex("^[a-z]{2}(-gov)?-[a-z]+-[0-9]$", var.aws_region))
    error_message = "aws_region must be a region code such as us-east-1."
  }
}

variable "availability_zones" {
  description = "The two availability zones (pinned, so a plan never moves subnets)."
  type        = list(string)
  default     = ["us-east-1a", "us-east-1b"]
  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "availability_zones must list exactly two zones."
  }
}

variable "project" {
  description = "Project name used in names and tags."
  type        = string
  default     = "collections-copilot"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}$", var.project))
    error_message = "project must be 3–31 lowercase letters, digits or hyphens."
  }
}

variable "environment" {
  description = "Environment name."
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be dev, staging or prod."
  }
}

variable "image_tag" {
  description = "Immutable image tag deployed to every service (a git SHA or release version)."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9._-]{1,128}$", var.image_tag)) && var.image_tag != "latest"
    error_message = "image_tag must be an immutable tag, never latest."
  }
}

variable "certificate_arn" {
  description = "ACM certificate for the console's hostname."
  type        = string
  validation {
    condition     = can(regex("^arn:aws:acm:[a-z0-9-]+:[0-9]{12}:certificate/.+$", var.certificate_arn))
    error_message = "certificate_arn must be an ACM certificate ARN."
  }
}

variable "public_hostname" {
  description = "Hostname the console is served on (OAuth callbacks and CC_PUBLIC_BASE_URL)."
  type        = string
  default     = "collections.example.com"
  validation {
    condition     = can(regex("^[a-z0-9.-]+\\.[a-z]{2,}$", var.public_hostname))
    error_message = "public_hostname must be a DNS name."
  }
}

variable "alarm_email" {
  description = "Address for alarm and budget notifications."
  type        = string
  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alarm_email))
    error_message = "alarm_email must be an e-mail address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly AWS cost budget in USD."
  type        = number
  default     = 150
  validation {
    condition     = var.monthly_budget_usd > 0 && var.monthly_budget_usd <= 10000
    error_message = "monthly_budget_usd must be between 0 and 10000."
  }
}

variable "bedrock_model_ids" {
  description = "Bedrock model ids the worker may invoke (CC_BEDROCK_MODEL_FAST and _REASONING)."
  type        = list(string)
  default     = ["anthropic.claude-haiku-5-5", "anthropic.claude-opus-5-5"]
  validation {
    condition     = length(var.bedrock_model_ids) > 0 && alltrue([for id in var.bedrock_model_ids : startswith(id, "anthropic.")])
    error_message = "Every bedrock_model_ids entry must start with \"anthropic.\"."
  }
}

variable "worker_max_tasks" {
  description = "Upper bound for worker autoscaling."
  type        = number
  default     = 10
  validation {
    condition     = var.worker_max_tasks >= 1 && var.worker_max_tasks <= 50
    error_message = "worker_max_tasks must be between 1 and 50."
  }
}

variable "mcp_gateway_image" {
  description = "mcp-gateway image pinned by tag or digest (null: this repository's build in ECR)."
  type        = string
  default     = null
  validation {
    condition     = var.mcp_gateway_image == null || can(regex("(:[A-Za-z0-9._-]{1,128}|@sha256:[a-f0-9]{64})$", var.mcp_gateway_image))
    error_message = "mcp_gateway_image must end in an explicit tag or an @sha256 digest."
  }
}

variable "smtp_host" {
  description = "SMTP relay for supervisor notifications (an SES SMTP endpoint, for example)."
  type        = string
  validation {
    condition     = length(var.smtp_host) > 0 && !contains(["localhost", "127.0.0.1"], var.smtp_host)
    error_message = "smtp_host must name a reachable relay, not localhost."
  }
}

variable "smtp_port" {
  description = "SMTP relay port."
  type        = number
  default     = 587
  validation {
    condition     = contains([25, 465, 587, 2465, 2587], var.smtp_port)
    error_message = "smtp_port must be an SMTP port (25, 465, 587, 2465 or 2587)."
  }
}
