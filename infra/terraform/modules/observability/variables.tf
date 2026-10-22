variable "project" {
  description = "Project name used in resource names."
  type        = string
}

variable "environment" {
  description = "Environment name."
  type        = string
}

variable "services" {
  description = "Services that get a log group."
  type        = list(string)
}

variable "kms_key_arn" {
  description = "KMS key for log groups and the SNS topic."
  type        = string
}

variable "log_retention_days" {
  description = "Log retention in days (30 for this environment)."
  type        = number
  default     = 30
  validation {
    condition     = var.log_retention_days >= 30
    error_message = "log_retention_days must be at least 30."
  }
}

variable "alarm_email" {
  description = "Address that receives alarm and budget notifications."
  type        = string
  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.alarm_email))
    error_message = "alarm_email must be an e-mail address."
  }
}

variable "monthly_budget_usd" {
  description = "Monthly cost budget in USD."
  type        = number
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "monthly_budget_usd must be positive."
  }
}

variable "queue_names" {
  description = "Work queue names by key (oldest-message alarm)."
  type        = map(string)
}

variable "dlq_names" {
  description = "Dead-letter queue names by key (DLQ alarm)."
  type        = map(string)
}

variable "alb_arn_suffix" {
  description = "Load balancer ARN suffix for the 5xx alarm."
  type        = string
}
