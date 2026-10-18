variable "bucket_name" {
  description = "Globally unique bucket name."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.bucket_name))
    error_message = "bucket_name must be 3–63 lowercase letters, digits, dots or hyphens."
  }
}

variable "kms_key_arn" {
  description = "KMS key for SSE-KMS."
  type        = string
}

variable "noncurrent_days" {
  description = "Days before non-current object versions expire."
  type        = number
  default     = 90
  validation {
    condition     = var.noncurrent_days >= 30
    error_message = "noncurrent_days must be at least 30."
  }
}

variable "expire_days" {
  description = "Days before an upload (the current version) expires."
  type        = number
  default     = 30
  validation {
    condition     = var.expire_days >= 1 && var.expire_days <= 365
    error_message = "expire_days must be between 1 and 365."
  }
}
