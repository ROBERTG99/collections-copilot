variable "name" {
  description = "Service and container name (api, worker, web, ledger-mcp, mcp-gateway)."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name))
    error_message = "name must be lowercase letters, digits and hyphens."
  }
}

variable "cluster_name" {
  description = "ECS cluster name."
  type        = string
}

variable "cluster_arn" {
  description = "ECS cluster ARN."
  type        = string
}

variable "image" {
  description = "Container image with an explicit tag or a sha256 digest (never :latest)."
  type        = string
  validation {
    condition     = can(regex("(:[A-Za-z0-9._-]{1,128}|@sha256:[a-f0-9]{64})$", var.image)) && !endswith(var.image, ":latest")
    error_message = "image must end in an explicit tag or @sha256 digest, and never use :latest."
  }
}

variable "command" {
  description = "Container command override (null keeps the image's CMD)."
  type        = list(string)
  default     = null
}

variable "cpu" {
  description = "Task CPU units."
  type        = number
  default     = 512
  validation {
    condition     = contains([256, 512, 1024, 2048, 4096], var.cpu)
    error_message = "cpu must be a Fargate CPU size (256, 512, 1024, 2048 or 4096)."
  }
}

variable "memory" {
  description = "Task memory in MiB."
  type        = number
  default     = 1024
  validation {
    condition     = var.memory >= 512 && var.memory <= 30720
    error_message = "memory must be between 512 and 30720 MiB."
  }
}

variable "container_port" {
  description = "Port the container listens on (null for workers with no listener)."
  type        = number
  default     = null
}

variable "environment" {
  description = "Plain environment variables (never secrets)."
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Environment variables injected from Secrets Manager, name to secret ARN."
  type        = map(string)
  default     = {}
}

variable "health_check_command" {
  description = "Container health check command (null for none)."
  type        = list(string)
  default     = null
}

variable "vpc_id" {
  description = "VPC id."
  type        = string
}

variable "vpc_cidr_block" {
  description = "VPC CIDR for in-VPC egress."
  type        = string
}

variable "subnet_ids" {
  description = "Private subnets for the tasks."
  type        = list(string)
}

variable "ingress_security_group_ids" {
  description = "Security groups allowed to reach the container port."
  type        = list(string)
  default     = []
}

variable "target_group_arn" {
  description = "Load balancer target group (null when not behind the ALB)."
  type        = string
  default     = null
}

variable "execution_role_arn" {
  description = "ECS task execution role."
  type        = string
}

variable "task_role_arn" {
  description = "Task role for the application's own AWS calls."
  type        = string
}

variable "log_group_name" {
  description = "CloudWatch log group (created, with KMS and retention, by the observability module)."
  type        = string
}

variable "service_connect_namespace_arn" {
  description = "Cloud Map namespace for Service Connect."
  type        = string
}

variable "autoscaling" {
  description = "Task count bounds, CPU target, and an optional queue to scale on (backlog per task)."
  type = object({
    min              = number
    max              = number
    cpu_target       = optional(number, 60)
    queue_name       = optional(string)
    backlog_per_task = optional(number, 20)
  })
  validation {
    condition     = var.autoscaling.min >= 1 && var.autoscaling.max >= var.autoscaling.min
    error_message = "autoscaling needs 1 <= min <= max."
  }
}

variable "extra_ports" {
  description = "More container ports published through Service Connect, by DNS name (e.g. an admin port)."
  type        = map(number)
  default     = {}
}

variable "internet_egress" {
  description = "Allow HTTPS to the internet through NAT (Twilio, Bedrock, Cognito)."
  type        = bool
  default     = true
}

variable "vpc_egress_ports" {
  description = "Ports the tasks may open inside the VPC: database, cache and Service Connect peers."
  type        = list(number)
  default     = [5432, 6379, 8300, 8310, 8343, 8344, 8348]
  validation {
    condition     = alltrue([for p in var.vpc_egress_ports : p > 0 && p < 65536])
    error_message = "vpc_egress_ports must be TCP ports."
  }
}
