variable "name" {
  description = "Name prefix for the database resources."
  type        = string
}

variable "vpc_id" {
  description = "VPC of the database security group."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnet ids for the DB subnet group (two availability zones)."
  type        = list(string)
  validation {
    condition     = length(var.subnet_ids) >= 2
    error_message = "subnet_ids must list at least two private subnets."
  }
}

variable "allowed_security_group_ids" {
  description = "Security groups of the tasks allowed to connect on 5432, by a static name."
  type        = map(string)
}

variable "kms_key_arn" {
  description = "KMS key for storage, the master secret and Performance Insights."
  type        = string
}

variable "engine_version" {
  description = "PostgreSQL major or minor version (16.x)."
  type        = string
  default     = "16"
  validation {
    condition     = can(regex("^16(\\.[0-9]+)?$", var.engine_version))
    error_message = "engine_version must be PostgreSQL 16."
  }
}

variable "instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.medium"
  validation {
    condition     = startswith(var.instance_class, "db.")
    error_message = "instance_class must be an RDS class such as db.t4g.medium."
  }
}

variable "allocated_storage_gb" {
  description = "Initial storage in GiB (autoscaling up to 4x)."
  type        = number
  default     = 20
  validation {
    condition     = var.allocated_storage_gb >= 20
    error_message = "allocated_storage_gb must be at least 20."
  }
}

variable "backup_retention_days" {
  description = "Automated backup retention in days (at least 7)."
  type        = number
  default     = 7
  validation {
    condition     = var.backup_retention_days >= 7 && var.backup_retention_days <= 35
    error_message = "backup_retention_days must be between 7 and 35."
  }
}

variable "multi_az" {
  description = "Run a standby in a second availability zone."
  type        = bool
  default     = false
}
