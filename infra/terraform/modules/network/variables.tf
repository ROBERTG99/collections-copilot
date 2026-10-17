variable "project" {
  description = "Project name used in resource names and tags."
  type        = string
}

variable "environment" {
  description = "Environment name (dev, prod)."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR block (a /16 or smaller prefix up to /20)."
  type        = string
  validation {
    condition     = can(cidrhost(var.cidr_block, 0)) && tonumber(split("/", var.cidr_block)[1]) <= 20
    error_message = "cidr_block must be a valid IPv4 CIDR with a prefix of /20 or shorter."
  }
}

variable "azs" {
  description = "Exactly two availability zones."
  type        = list(string)
  validation {
    condition     = length(var.azs) == 2
    error_message = "azs must list exactly two availability zones."
  }
}

variable "kms_key_arn" {
  description = "KMS key for the flow-log group."
  type        = string
}

variable "log_retention_days" {
  description = "Flow-log retention in days."
  type        = number
  default     = 30
  validation {
    condition     = var.log_retention_days >= 30
    error_message = "log_retention_days must be at least 30."
  }
}
