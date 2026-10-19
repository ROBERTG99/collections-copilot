variable "queues" {
  description = "Queues to create, by name, with their visibility timeout and retention."
  type = map(object({
    visibility_timeout_seconds = optional(number, 120)
    message_retention_seconds  = optional(number, 345600)
  }))
  validation {
    condition     = alltrue([for name, q in var.queues : q.visibility_timeout_seconds >= 30 && q.visibility_timeout_seconds <= 43200])
    error_message = "visibility_timeout_seconds must be between 30 and 43200."
  }
}

variable "kms_key_arn" {
  description = "KMS key for server-side encryption of every queue."
  type        = string
}

variable "max_receive_count" {
  description = "Receives before a message moves to its dead-letter queue."
  type        = number
  default     = 5
  validation {
    condition     = var.max_receive_count >= 1 && var.max_receive_count <= 10
    error_message = "max_receive_count must be between 1 and 10."
  }
}
