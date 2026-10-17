variable "project" {
  description = "Project name used in key descriptions and aliases."
  type        = string
}

variable "environment" {
  description = "Environment name."
  type        = string
}

variable "deletion_window_days" {
  description = "Days a scheduled key deletion waits (7–30)."
  type        = number
  default     = 30
  validation {
    condition     = var.deletion_window_days >= 7 && var.deletion_window_days <= 30
    error_message = "deletion_window_days must be between 7 and 30."
  }
}
