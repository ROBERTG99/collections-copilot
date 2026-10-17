variable "name" {
  description = "User pool name."
  type        = string
}

variable "domain_prefix" {
  description = "Hosted UI domain prefix (globally unique)."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", var.domain_prefix))
    error_message = "domain_prefix must be lowercase letters, digits and hyphens."
  }
}

variable "callback_urls" {
  description = "OAuth callback URLs (HTTPS only)."
  type        = list(string)
  validation {
    condition     = length(var.callback_urls) > 0 && alltrue([for u in var.callback_urls : startswith(u, "https://")])
    error_message = "callback_urls must be non-empty and every URL must use https://."
  }
}

variable "logout_urls" {
  description = "Sign-out redirect URLs (HTTPS only)."
  type        = list(string)
  validation {
    condition     = alltrue([for u in var.logout_urls : startswith(u, "https://")])
    error_message = "logout_urls must use https://."
  }
}

variable "mfa" {
  description = "MFA requirement: OPTIONAL or ON."
  type        = string
  default     = "OPTIONAL"
  validation {
    condition     = contains(["OPTIONAL", "ON"], var.mfa)
    error_message = "mfa must be OPTIONAL or ON."
  }
}
