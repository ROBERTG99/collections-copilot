variable "prefix" {
  description = "Path prefix for secret names, for example collections-copilot/dev."
  type        = string
}

variable "secret_names" {
  description = "Secret names to create, without values."
  type        = list(string)
  validation {
    condition     = alltrue([for n in var.secret_names : can(regex("^[a-z0-9_-]+$", n))])
    error_message = "secret_names may contain only lowercase letters, digits, underscores and hyphens."
  }
}

variable "kms_key_arn" {
  description = "KMS key that encrypts every secret."
  type        = string
}

variable "recovery_window_days" {
  description = "Days a deleted secret can be restored (7–30)."
  type        = number
  default     = 7
  validation {
    condition     = var.recovery_window_days >= 7 && var.recovery_window_days <= 30
    error_message = "recovery_window_days must be between 7 and 30."
  }
}
