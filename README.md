# Akamai Property Terraform skill

An [Agent Skill](https://agentskills.io) that lets your AI coding agent create and change Akamai
properties as Terraform from plain-English requests, checking the result against Akamai before
anything is applied.

```
You:    Generate an Akamai property as Terraform: product Dynamic Site Accelerator,
        origin www.example.com, hostname example.agenobarb.cloud, default caching settings

Agent:  I've generated the Terraform in example.agenobarb.cloud/ and terraform plan
        ran cleanly. Nothing has been applied.

        Checks
        - Schema check passed for prd_Site_Accel / latest. It doesn't cover contract
          entitlements or rules PAPI only runs at apply, so apply could still fail.
        - terraform validate passed.
        - Plan: 6 to add, 0 to change, 0 to destroy:
          - akamai_cp_code.my_cp: new CP code example.agenobarb.cloud
          - akamai_property.my-property: example.agenobarb.cloud →
            example.agenobarb.cloud.edgekey.net, Secure by Default (DV) certificate
          - akamai_dns_record.property-record: Edge DNS CNAME to the edge hostname
          - akamai_dns_record.dv-validation: CNAME for the certificate's DV challenge
          - random_id.debug_key: Enhanced Debug key, kept only in Terraform state
          - akamai_property_activation: activates on STAGING, not production
        - Nothing existing gets replaced or destroyed.

        Before you apply
        - notification_emails is still the placeholder noreply@example.com. Add your
          address to terraform.tfvars if you want activation emails and I'll re-plan.
        - The CNAME points the public name at Akamai as soon as it's created, even
          though the property is only on staging. That's how Secure by Default
          validates the domain.

        Should I run terraform apply tfplan?
```

## Why use it

Agents write decent Terraform, but Akamai properties trip them up. UI names don't match API
names, behaviors change faster than model training data, products offer different behaviors, and Akamai only rejects a bad rule tree at
`terraform apply`, one error at a time.

With this skill, the agent:

- **looks up every behavior in Akamai's techdocs** instead of writing it from memory, using a
  catalog that maps UI names to Terraform and API names;
- **validates the whole rule tree against your product's schema** (live, via the Property
  Manager API) before planning, so "not available for this product" shows up before apply;
- **starts new properties from the rule set Akamai's UI creates** for Ion, Dynamic Site
  Acceleration, Adaptive Media Delivery, Object Delivery or Download Delivery;
- **asks rather than guesses** when a request is ambiguous or a behavior isn't available, and
  **waits for your yes before every `terraform apply`**. Activations go to staging unless you ask
  for production.

## Is it for you?

Try it to manage Akamai properties with AI agent.

It's probably not a fit if:

- your DNS isn't on **Akamai Edge DNS**: new properties create their DNS records there;
- your account doesn't have **Secure by Default**: new properties use it for certificates;

Existing properties are fine, including ones exported with `akamai terraform export-property`; the
agent keeps their structure and style.

## Requirements

- An agent that supports Agent Skills and runs commands locally: Claude Code, Codex, GitHub
  Copilot, Cursor, Gemini CLI and [others](https://agentskills.io/clients)
- Python 3.10+, [Terraform](https://developer.hashicorp.com/terraform/install),
  [jq](https://jqlang.org/download/)
- Akamai API credentials in `~/.edgerc` (section `[default]`) with Property Manager and Edge DNS
  access

## Install

**Claude Code:**

```bash
git clone https://github.com/yyakubov-akam/akamai-property-terraform ~/.claude/skills/akamai-property-terraform
pip install requests edgegrid-python jsonschema
```

Claude Code picks the skill up automatically (restart it once if `~/.claude/skills` is new). For a
single project, clone into `<project>/.claude/skills/akamai-property-terraform` instead. Update with
`git pull`.

**Other agents:** clone the same repo into your agent's skills folder and run the same
`pip install`. See [Codex](https://developers.openai.com/codex/skills/),
[GitHub Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills),
[Cursor](https://cursor.com/docs/context/skills) or
[Gemini CLI](https://geminicli.com/docs/cli/skills/). Chat apps that run skills in a cloud sandbox
won't work: the skill needs your credentials and Terraform.

**Check your setup** from the skill folder:

```bash
python3 scripts/validate_rules.py references/template/rules/ion/main.json \
    --product prd_SPM --var cp_code_id=12345 --var cp_code_name=test
```

`OK: rule tree is valid for prd_SPM / latest` means Python, credentials and API access all work.
Otherwise the message says what's missing (usually `~/.edgerc`).

## Try it

Open your agent in the folder with your property's Terraform, or in an empty folder for a new one:

- *Create a property for shop.example.com, origin origin.example.com, product Ion*
- *Configure Cloud Monitor for this property, destination logs.example.com*
- *Don't cache anything under /api/\**
- *Ignore utm_\* query parameters in the cache key*

Name the product for new properties, say "production" when you mean it, and give an email for
activation notifications (the default is a placeholder). Before a production activation, replace
the placeholder ticket in `main.tf`'s `compliance_record`.

Some errors only appear at apply, such as a feature your contract doesn't include. The agent reads
the error, fixes what it can, and asks again before the next apply.

How it all works is in [SKILL.md](SKILL.md).

## Feedback

Open an issue with your prompt, what the agent did, and what you expected. Most useful: the agent
stating something as fact that turned out wrong, or changing something without asking.
