variable "group_name" {
  type        = string
  description = "The Akamai group name to target"
  default     = "default_group" # Fallback if no tfvars is provided
}

variable "dns_zone" {
  type    = string
  default = "example.com"
}

variable "hostname" {
  type    = string
  default = "test-property"
}

variable "property_name" {
  description = "Property name. Defaults to the property hostname (hostname.dns_zone) if null."
  type        = string
  default     = null
}

variable "cpcode_name" {
  description = "CP code name. Defaults to the property name if null. Characters other than letters, digits, '.' and '-' are replaced with '-'."
  type        = string
  default     = null
}

variable "notification_emails" {
  description = "Email addresses notified about property activations"
  type        = list(string)
  default     = ["noreply@example.com"] # placeholder: replace with a real address to get notifications

  validation {
    condition     = length(var.notification_emails) > 0
    error_message = "At least one email should be provided."
  }
}

variable "delivery_product" {
  # Which Akamai CDN product to use: https://techdocs.akamai.com/terraform/docs/common-identifiers#product-ids
  type    = string
  default = "prd_SPM" # Ion
  # Must match the product whose baseline was copied into rules/ (the skill's
  # baselines are in rules/<product>/), and the rule tree must be validated
  # against this product before apply.
}

variable "debug_key" {
  description = "Enhanced Debug key, 64 hex characters. Null generates one for this property."
  type        = string
  default     = null
  sensitive   = true

  validation {
    condition     = var.debug_key == null || can(regex("^[0-9a-fA-F]{64}$", var.debug_key))
    error_message = "debug_key must be 64 hex characters."
  }
}

variable "activation_network" {
  type        = string
  description = "Akamai network to activate the property on: STAGING or PRODUCTION"
  default     = "STAGING" # production is a deliberate choice, not a default

  validation {
    condition     = contains(["STAGING", "PRODUCTION"], var.activation_network)
    error_message = "activation_network must be \"STAGING\" or \"PRODUCTION\"."
  }
}


