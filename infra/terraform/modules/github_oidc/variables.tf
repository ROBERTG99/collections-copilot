variable "name" {
  description = "Name prefix for the role."
  type        = string
}

variable "github_repository" {
  description = "owner/repo allowed to assume the plan role (main branch only)."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "github_repository must look like owner/repo."
  }
}

variable "state_bucket_arn" {
  description = "Terraform state bucket the plan may read (null while the backend is local)."
  type        = string
  default     = null
}
