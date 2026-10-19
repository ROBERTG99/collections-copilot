variable "environment" {
  description = "Environment name."
  type        = string
}

variable "services" {
  description = "Services that get a task role (api, worker, web, ledger-mcp, mcp-gateway)."
  type        = list(string)
}

variable "service_secret_arns" {
  description = "Secrets each service's execution role may read (its task definition's secrets)."
  type        = map(list(string))
}

variable "data_key_arn" {
  description = "Data KMS key (secrets, queues, uploads)."
  type        = string
}

variable "signing_key_arn" {
  description = "RSA signing key for agent JWTs (kms:Sign for the api and the worker only)."
  type        = string
}

variable "queue_arns" {
  description = "Work queue ARNs by name."
  type        = map(string)
}

variable "dlq_arns" {
  description = "Dead-letter queue ARNs by source queue."
  type        = map(string)
}

variable "uploads_bucket_arn" {
  description = "Uploads bucket ARN."
  type        = string
}

variable "bedrock_model_ids" {
  description = "Bedrock model ids the worker may invoke (anthropic.* only)."
  type        = list(string)
  validation {
    condition     = length(var.bedrock_model_ids) > 0 && alltrue([for id in var.bedrock_model_ids : startswith(id, "anthropic.")])
    error_message = "bedrock_model_ids must be non-empty and every id must start with \"anthropic.\"."
  }
}
