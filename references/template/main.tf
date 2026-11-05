terraform {
  required_providers {
    akamai = {
      source  = "akamai/akamai"
      version = "10.3.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "3.9.1"
    }
  }
  required_version = ">= 1.0"
}

provider "akamai" {
  edgerc         = "~/.edgerc"
  config_section = "default"
}

# Fetch contract ID for my group name
data "akamai_contract" "my_contract" {
  group_name = var.group_name
}

# Fetch group ID using the group name and contract ID
data "akamai_group" "my_group" {
  group_name  = var.group_name
  contract_id = data.akamai_contract.my_contract.id
}

output "contract_id" {
  value = data.akamai_contract.my_contract.id
}

output "group_id" {
  value = data.akamai_group.my_group.id
}

# Names default to the property hostname, e.g. test-property.example.com,
# so each property built from this template gets its own. The CP code matters
# most: the provider reuses an existing CP code with the same name instead of
# creating one, so a fixed name would silently share one CP code (and its
# reporting and billing) across properties. CP code names can't contain
# commas, underscores, quotes, ^, # or %, so anything but letters, digits,
# '.' and '-' is replaced, as in Akamai's terraform-templates delivery module.
locals {
  property_hostname = "${var.hostname}.${var.dns_zone}"
  property_name     = coalesce(var.property_name, local.property_hostname)
  cp_code_name      = replace(coalesce(var.cpcode_name, local.property_name), "/[^a-zA-Z0-9.-]/", "-")
}

# Create Content Provider (CP) code
resource "akamai_cp_code" "my_cp" {
  name        = local.cp_code_name
  group_id    = data.akamai_group.my_group.id
  contract_id = data.akamai_contract.my_contract.id
  product_id  = var.delivery_product
}

# No akamai_edge_hostname resource: for Enhanced TLS (.edgekey.net) it always
# requires a pre-existing `certificate` (CPS enrollment ID), API-side, no
# matter what cert_provisioning_type the property uses -- so it can't be
# created ahead of a Secure by Default cert that doesn't exist yet. Akamai's
# own reference template (github.com/akamai/terraform-templates-modules,
# delivery/main.tf) skips this resource entirely for etls + secure_by_default
# and instead references the future edge hostname as a plain string; Property
# Manager creates the edge hostname and the DV cert together on activation.
locals {
  edge_hostname_domain = "${local.property_hostname}.edgekey.net"
}

# Point the hostname at the (not-yet-created) edge hostname so Akamai can see
# domain control for Secure by Default validation.
resource "akamai_dns_record" "property-record" {
  zone       = var.dns_zone
  name       = local.property_hostname
  recordtype = "CNAME"
  target     = [local.edge_hostname_domain]
  ttl        = 300
}

# Enhanced Debug key: the baselines that enable enhancedDebug need one, and
# PAPI rejects an empty key at activation (the schema allows it, so Verify
# doesn't catch it). Generated once per property and kept in state, rather
# than written into the rule files, because it's a secret: tokens for the
# Akamai-Debug request header are derived from it. Techdocs allows reusing one
# key across properties -- set var.debug_key for that. Rotate with
# `terraform apply -replace=random_id.debug_key`.
resource "random_id" "debug_key" {
  byte_length = 32 # 64 hex characters, the length of a UI-generated key
}

# Default rule tree for the property, assembled from rules/main.json and its
# #include'd behavior files: https://techdocs.akamai.com/terraform/docs/pm-ds-rules-template
# In a generated project, rules/ holds one product's baseline, copied from
# this template's rules/<product>/.
# cp_code_id/cp_code_name are threaded in as template variables because
# they're only known from akamai_cp_code.my_cp at apply time, and debug_key
# because it's a secret -- everything else in the rule tree is static and
# lives directly in the JSON files. A baseline that doesn't use a variable
# (Ion has no enhancedDebug) ignores it.
data "akamai_property_rules_template" "default_rule" {
  template_file = abspath("${path.module}/rules/main.json")

  variables {
    name  = "cp_code_id"
    type  = "number"
    value = akamai_cp_code.my_cp.id
  }

  variables {
    name  = "cp_code_name"
    type  = "string"
    value = akamai_cp_code.my_cp.name
  }

  variables {
    name  = "debug_key"
    type  = "string"
    value = coalesce(var.debug_key, random_id.debug_key.hex)
  }
}

# Provision Akamai properties guide: https://techdocs.akamai.com/terraform/docs/set-up-property-provisioning
# Secure by Default (DEFAULT cert_provisioning_type): Akamai auto-provisions
# and auto-renews a DV cert for this hostname after activation. Requires the
# account to have Secure by Default access -- if it doesn't, expect an
# explicit error here or on activation, not a silent non-issuance.
resource "akamai_property" "my-property" {
  name        = local.property_name
  product_id  = var.delivery_product
  contract_id = data.akamai_contract.my_contract.id
  group_id    = data.akamai_group.my_group.id

  hostnames {
    cname_from             = local.property_hostname
    cname_to               = local.edge_hostname_domain
    cert_provisioning_type = "DEFAULT"
  }

  # rule_format omitted: the provider defaults to the latest rule format,
  # which is what the Akamai wizard also targets ("ruleFormat": "latest").
  rules = data.akamai_property_rules_template.default_rule.json
}

# Secure by Default validates domain ownership via a DNS CNAME challenge
# (a different subsystem from classic CPS -- nothing shows up in CPS for
# this). The challenge details are already computed on the property's own
# hostnames.cert_status once it's created. `hostnames` and `cert_status` are
# both `set` blocks (unordered, not indexable -- hence one(), not [0]).
# one() just unwraps the single element; this config only ever declares one
# hostnames{} block, so no filtering by cname_from is needed to find it.
locals {
  dv_challenge = one(one(akamai_property.my-property.hostnames).cert_status)
}

resource "akamai_dns_record" "dv-validation" {
  zone       = var.dns_zone
  name       = local.dv_challenge.hostname
  recordtype = "CNAME"
  target     = [local.dv_challenge.target]
  ttl        = 300
}

# Activate the property on var.activation_network (defaults to STAGING).
resource "akamai_property_activation" "tf-test-property-staging" {
  property_id                    = akamai_property.my-property.id
  contact                        = var.notification_emails
  version                        = akamai_property.my-property.latest_version
  network                        = var.activation_network
  note                           = "Activation via Terraform"
  auto_acknowledge_rule_warnings = true

  compliance_record {
    noncompliance_reason_no_production_traffic {
      ticket_id = "123"
    }
  }

  timeouts {
    default = "1h"
  }
}
