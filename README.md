# Akamai Property Terraform skill

Ask your AI coding agent for Akamai property changes in plain English, and get
Terraform that's checked against Akamai before anything goes live.

```
You:    Create a property for shop.example.com, origin origin.example.com,
        product Dynamic Site Acceleration
Agent:  DSA doesn't offer two behaviors in the default rule set (Adaptive
        acceleration, Script management). Remove them, or switch to Ion?
You:    Remove them
Agent:  Plan: 5 resources to add, activation on STAGING. Apply?
```

> **Work in progress.** Tested with Claude Code on Dynamic Site Acceleration
> and Ion. Please report what breaks — see [Feedback](#feedback).

## Why this exists

AI agents are good at Terraform, but Akamai properties trip them up:

- **Names don't line up.** What the Akamai UI calls "Cloud Monitor
  Instrumentation" is `edge_connect` in Terraform and `edgeConnect` in the API.
  An agent guessing from memory often picks the wrong one.
- **Options change.** Akamai has over 200 behaviors and updates them
  regularly, so what the model learned in training is often out of date.
- **Products differ.** A behavior that works on Ion may not exist on Dynamic
  Site Acceleration.
- **Mistakes show up late.** Akamai only checks the configuration at
  `terraform apply`, after the property has already been created. You find out
  one error at a time.

This skill gives the agent Akamai's own documentation to work from, a way to
check the configuration against your product before applying it, and clear
rules about when to stop and ask you.

## What you get

- **Plain-English requests.** Use the names you know from the Akamai UI:
  "add Cloud Monitor", "enable SiteShield", "don't cache /api/*".
- **Configuration based on Akamai's docs**, not on the model's memory.
- **Problems caught before apply**, like a behavior your product doesn't offer.
- **You stay in control.** The agent asks when a choice is yours, shows what
  will change, and waits for your yes before every apply. New properties
  activate on staging unless you ask for production.
- **A solid starting point.** New properties start from Akamai's recommended
  default rule set, adjusted for your product.

## What you need

- An AI coding agent that supports [Agent Skills](https://agentskills.io) and
  can run commands on your machine — Claude Code, Codex, GitHub Copilot,
  Cursor, Gemini CLI and others
- Python 3.10 or later, [Terraform](https://developer.hashicorp.com/terraform/install)
  and [jq](https://jqlang.org/download/)
- Akamai API credentials in `~/.edgerc` (section `[default]`) with access to
  Property Manager and Edge DNS
- Your domain's DNS zone on Akamai Edge DNS — other DNS providers won't work

## Install

### Claude Code

```bash
git clone <repo-url> ~/.claude/skills/akamai-property-terraform
pip install requests edgegrid-python jsonschema
```

That's it — Claude Code picks up the skill automatically and uses it whenever
you ask for an Akamai property change. (If `~/.claude/skills` didn't exist
before, restart Claude Code once.) To update later, run `git pull` in that
folder.

To use it in one project only, clone it into that project's
`.claude/skills/akamai-property-terraform` instead. Either way, keep the folder
name. See Claude Code's [skills docs](https://code.claude.com/docs/en/skills)
for more.

### Other agents

This skill follows the open [Agent Skills](https://agentskills.io) format, so
the same folder works in other agents — only the location to put it in
differs. Clone the repo where your agent looks for skills, and run the same
`pip install`:

- [OpenAI Codex](https://developers.openai.com/codex/skills/)
- [GitHub Copilot](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills)
  and [VS Code](https://code.visualstudio.com/docs/copilot/customization/agent-skills)
- [Cursor](https://cursor.com/docs/context/skills)
- [Gemini CLI](https://geminicli.com/docs/cli/skills/)
- [All agents that support Agent Skills](https://agentskills.io/clients)

The agent must be able to run commands on your computer, because the skill
uses your Akamai credentials and Terraform. Chat apps that run skills in a
cloud sandbox won't work. So far the skill has only been tested with Claude
Code.

### Check that it works

In the skill folder, run:

```bash
python3 scripts/validate_rules.py references/template/rules/main.json \
    --product prd_SPM --var cp_code_id=12345 --var cp_code_name=test
```

If you see `OK: rule tree is valid for prd_SPM / latest`, your Python
packages, credentials and Akamai connection all work. If not, the message says
what's missing — usually `~/.edgerc`.

## Use it

Open your agent in the folder where your property's Terraform lives, or in an
empty folder for a new property, and ask:

- *Create a property for hostname shop.example.com, origin origin.example.com, delivery product Dynamic Site Acceleration*
- *Configure Cloud Monitor for this property. Destination hostname: logs.example.com*
- *Don't cache anything under /api/\**
- *Ignore utm_\* query parameters in the cache key*

Expect a question or two along the way. Before changing anything in your
Akamai account, the agent shows a summary like this and waits:

```
- akamai_property.my-property — adds an "API" rule matching /api/*, caching NO_STORE
- akamai_property_activation — activates the new version
- 0 to add, 2 to change, 0 to destroy
- Activation network: STAGING
```

A few tips:

- **Name the product** for new properties ("Dynamic Site Acceleration", "Ion").
- **Say "production"** when you mean it. Otherwise changes activate on staging.
- **Give your email** if you want activation notifications; the default is a
  placeholder.

## Good to know

- **New properties** use Secure by Default certificates, so your account needs
  Secure by Default access.
- **Some errors only appear at apply** — for example when your contract doesn't
  include a feature. The agent reads the error, fixes what it can or tells you
  what it can't, and asks again before the next apply.
- **Property Manager only.** Security configurations (WAF, App & API
  Protector) and certificates beyond Secure by Default aren't covered.
- **Before a production activation**, check the `compliance_record` in
  `main.tf`; it holds a placeholder ticket number.

Want to know how it all works? Everything the agent follows is in
[SKILL.md](SKILL.md).

## Feedback

Open an issue with the prompt you used, what the agent did, and what you
expected. Especially useful: the agent stating something as fact that turned
out to be wrong, or changing something without asking.
