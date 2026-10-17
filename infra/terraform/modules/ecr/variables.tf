variable "prefix" {
  description = "Repository name prefix, for example collections-copilot."
  type        = string
}

variable "repositories" {
  description = "Repository names (api, worker, web, ledger-mcp)."
  type        = list(string)
  validation {
    condition     = length(var.repositories) > 0
    error_message = "repositories must not be empty."
  }
}

variable "kms_key_arn" {
  description = "KMS key for image encryption."
  type        = string
}

variable "keep_images" {
  description = "Images kept per repository."
  type        = number
  default     = 30
  validation {
    condition     = var.keep_images >= 5
    error_message = "keep_images must be at least 5 (rollback needs older images)."
  }
}
