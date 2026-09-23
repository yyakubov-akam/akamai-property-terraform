---
name: akamai-property-terraform
description: >-
  Generate and modify Akamai property configurations as Terraform, grounded in
  live Akamai techdocs. Use this whenever the user asks to create, provision, or
  change an Akamai property, delivery configuration, or CDN configuration — or to
  add, remove, or configure any property behavior or criterion (caching, origin,
  redirects, headers, EdgeWorkers, Origin IP ACL, SureRoute, tiered distribution,
  and so on) — or mentions akamai_property, rules.tf, Property Manager, or PAPI
  together with Terraform or infrastructure as code. Use it even when the user
  names a behavior in plain English rather than by its Terraform identifier, and
  even when they do not mention Terraform explicitly but are clearly asking for an
  Akamai property change to be expressed as code.
---

# Akamai Property Terraform

Generate Terraform for Akamai properties, grounded in the official techdocs and
checked against PAPI before anything is applied. Nothing is assumed from recall.

The workflow has three steps, each backed by exactly one source:

| Step | Source | Answers |
|---|---|---|
| **Find** | `references/catalog.tsv` | What is this behavior called, and which page documents it? |
| **Learn** | the techdocs page (`scripts/fetch_doc.py`) | How is it written: options, values, dependencies? |
| **Verify** | PAPI product schema (`scripts/validate_rules.py`) | Is the whole rule tree valid for this product? |

Then **Apply**: `terraform init` → `validate` → `plan` → the user's explicit
approval → `apply`, looping back on errors. PAPI runs some checks only at
apply, so the job isn't done until an approved apply succeeds or the user
decides to stop.

## Start here

Read `references/template/` before writing or editing anything. It is a working,
tested property project and is the structural basis for new work as well as the
reference for existing conventions.

**The template's rule tree was built for Ion (`prd_SPM`).** Other products don't
offer every behavior in it — Dynamic Site Acceleration (`prd_Site_Accel`) has
neither `adaptiveAcceleration` nor `scriptManagement`, for example. So:

- Behaviors already in the template need no documentation lookup; substitute
  values and move on.
- Whatever the product, the assembled rule tree must pass **Verify** before
  apply — template behaviors included. "The template is tested" only means it
  is valid for Ion.

If the product the user names doesn't map to a product ID with certainty, ask.

## Find: resolve the name through the catalog

Use this for any behavior, criterion, resource or data source the template does
not already contain. Never construct a techdocs URL by hand; slugs don't
reliably follow identifiers (`edge_worker` → `edgeworker.md`).

`references/catalog.tsv` has one tab-separated line per techdocs page:

| Column | Example | Notes |
|---|---|---|
| `kind` | `behavior` | also `criterion`, `resource`, `data_source`, `guide` |
| `identifier` | `edge_connect` | Terraform snake_case name |
| `papi_name` | `edgeConnect` | the `name` used in rule JSON; don't derive it yourself (`allowHTTPSCacheKeySharing`, `gRPC`) |
| `display_name` | `Cloud Monitor Instrumentation` | the name in the Akamai UI, which users often quote |
| `version`, `includes` | `v2026-07-21`, `yes` | see below |
| `url` | `https://techdocs.akamai.com/...` | the page to fetch |
| `note` | `-` | warnings about the page's samples, see below |
| `description` | `Configures traffic logs for...` | the page's opening paragraph |

1. **Turn the request into search fragments.** Users quote UI names ("Cloud
   Monitor", "SiteShield") or describe an effect ("restrict origin to Akamai
   IPs"). Grep for several fragments at once:

   ```bash
   grep -i "cloud monitor" references/catalog.tsv | cut -f1-4,8
   grep -i -E "origin.*(acl|access|restrict|limit)|(restrict|limit).*origin" references/catalog.tsv | cut -f1-4,8
   ```

2. **If grep finds nothing convincing, read the names-only view in full** (about
   4k tokens) and match by meaning, then grep the candidates for their
   descriptions:

   ```bash
   grep -v '^#' references/catalog.tsv | cut -f1,2,4
   ```

3. **If several entries plausibly match, or none do, stop and ask.** Show the
   candidates with their display names and descriptions. "Restrict origin to
   Akamai IPs" matches both `origin_ip_acl` and `site_shield` — different
   features with different setup. Do not pick silently.

   Describe each option only with what the catalog and the fetched pages say.
   Many features also need work outside Terraform — origin firewall rules,
   certificates, DNS. If the page covers that, pass it on; if it doesn't, say
   the docs don't cover it. Never tell the user no further setup is needed on
   the strength of recall: restricting origin access, for example, ultimately
   happens on the origin's own firewall, which no property behavior configures.

4. **Respect the `note` column:**
   - `wrong-sample:<x>` — the page's samples belong to `<x>`, not this behavior
     (a techdocs error). Don't copy them or cite their values as defaults;
     build the behavior from the page's Options table and let Verify confirm it.
   - `no-sample` — the page has no JSON sample, so `papi_name` is unconfirmed.
     Build from the Options table; Verify confirms the name.

5. **Don't read `version` as "available since".** It is the rule format the docs
   were generated for and is the same on almost every row. Whether a behavior
   is offered for the property's product is what Verify checks. `includes`
   says whether the behavior may be used in an include.

## Learn: fetch the page

```bash
python scripts/fetch_doc.py <url from the catalog>
python scripts/fetch_doc.py --name <identifier | papiName | "Display Name"> [--kind behavior]
```

`--name` fails loudly on an ambiguous match and prints a warning for rows with
a `note`.

**Treat the fetched page as authoritative** for option names, nesting, allowed
values, defaults, and prose dependencies ("make sure you enable the `http2`
behavior"). Prefer it over recall and over similar-looking blocks in the
template.

Pages show two samples, HCL (`akamai_property_rules_builder`) and JSON. Copy
the one that matches how the property's rules are written:

- **JSON rule files** — new properties (the template) and existing ones built on
  `akamai_property_rules_template`, `jsonencode(...)` or a rules `.json` file.
  Copy the JSON sample and use `papi_name` as the `name`. Behavior samples are
  wrapped in `"behaviors": [ ... ]`; take the object inside. Criterion samples
  are wrapped in `"criterion": [ ... ]`, but the rule key is `"criteria"`, as in
  the template; take the object inside and put it under `"criteria"`.
- **`akamai_property_rules_builder` HCL** — typical of properties exported with
  `akamai terraform export-property`. Copy the HCL sample (snake_case, the
  catalog's `identifier`) and match the rule format block (`rules_v…`) the
  existing config already uses.

Don't convert an existing property from one style to the other unless the user
asks. New properties use the template's JSON style: its names are PAPI's own
camelCase, the same names Verify checks, so nothing needs translating.

If a fetch fails, say the lookup failed. Do not fall back to generating
plausible-looking syntax from memory — that is the exact failure this skill
exists to prevent.

## Verify: validate the rule tree before apply

Required for every new property and every rule change, whatever the product.
`terraform plan` cannot catch these errors: the provider submits the rule tree
to PAPI only at apply time, after the property and its first version exist.

```bash
python scripts/validate_rules.py <tf dir>/rules/main.json --product <product_id> \
    --var cp_code_id=12345 --var cp_code_name=placeholder
```

It assembles the tree the way `akamai_property_rules_template` does (resolving
`#include:` paths from the top-level template's directory and substituting
`${env.*}` variables), fetches the product's rule schema from PAPI with
`~/.edgerc`, and checks the entire tree.

- Pass a `--var` for every `variables {}` block of the
  `akamai_property_rules_template` data source, typed as declared: `12345` is a
  number, `true` a bool, anything else a string. Placeholders are fine — the real
  CP code ID doesn't exist before apply.
- Pass `--rule-format` only if the property pins one; the template leaves it
  unset, which means `latest`.
- Pass `--section` / `--account-switch-key` if the user's `provider "akamai"`
  block uses a non-default section or an account key; match it.
- Exit status: `0` valid, `1` invalid, `2` could not validate.

**If the rules aren't an `#include` template** — inline JSON, `jsonencode(...)`,
`rules_builder`, or an existing property's own layout — write the rendered tree
(`{"rules": {...}}`) to a scratch file and validate that file; a tree without
includes or variables needs no `--var`. For `rules_builder` and `jsonencode`
configs, the rendered tree is the planned `rules` value (run after the Apply
step's `terraform plan -out=tfplan`):

```bash
terraform show -json tfplan \
  | jq -r '.resource_changes[] | select(.type == "akamai_property") | .change.after.rules' > rendered.json
```

If that prints `null`, the rules depend on values known only at apply (like a
new CP code's ID); validate the source rule files with `--var` placeholders
instead.

**What exit `0` does and doesn't mean.** It means the tree passes the product's
PAPI schema — the check that rejects most bad applies (unknown behaviors,
behaviors the product doesn't offer, bad option names, types and values). It
does not see:

- **contract entitlements** — the schema is per product, not per contract, so a
  behavior needing a module the contract lacks still passes here;
- **PAPI's rule validation after the schema check** — cross-behavior
  dependencies (`adaptiveAcceleration` needs `http2`) and warnings.

Those can still fail at apply, which is why the Learn step's prose
dependencies must be honored. Report exit `0` as "passes the product schema",
not "will apply cleanly".

**On "Not available for <product>": stop and ask the user.** The options are to
remove the behavior, substitute an available one (found through the catalog),
or change the product. Don't drop it silently — it may be the reason the user
wanted this configuration, or the product may be the mistake. If removing
behaviors leaves a rule with no behaviors and no children, remove that rule
(with its criteria) too, and tell the user which rules went.

**On other schema errors**, fix them against the fetched page and rerun.

**If validation can't run** (exit `2`: missing credentials, network, unknown
product ID), say so plainly and don't describe the configuration as valid.

## Modifying an existing property

1. Read the existing `rules.tf` / `rules/*.json` (or equivalent) in full before
   editing.
2. Match its existing structure, rule nesting, and naming conventions.
3. Insert the new behavior into the appropriate existing rule rather than
   appending a new top-level rule, unless the user asks for a new rule.
   Before changing or adding a behavior, find every other place the same
   behavior appears in the tree. Behaviors in the default rule apply to all
   requests "unless another rule overrides the settings" (techdocs): the same
   behavior in a matching child rule, or a later matching sibling, replaces it
   for the requests that rule matches. Work out which requests the change
   actually reaches, tell the user about any rule that overrides it, and ask
   whether to update that rule too. Also say when the change has no effect on
   some requests — a cache key setting on content marked `NO_STORE`, for
   example.
4. Run Verify against the property's product.
5. Show what changed and where, so the diff is reviewable.
6. Run the Apply cycle. In the plan summary, call out anything that replaces
   or destroys an existing resource.

## Apply: init, validate, plan, ask, apply

Run this in the Terraform directory after every change, once Verify passes.
Everything up to `plan` is read-only; `apply` writes to the user's Akamai
account and activates the property, so it needs an explicit yes every time.

1. **Format and initialize.**

   ```bash
   terraform fmt
   terraform init     # first run, or after changing providers or their versions
   ```

2. **Validate the configuration:** `terraform validate`. Fix what it reports
   (HCL syntax, unknown arguments, failed variable validations) and rerun.

3. **Plan into a file:** `terraform plan -out=tfplan`. Plan reads from the
   Akamai APIs with the user's credentials but changes nothing. If it can't run
   (credentials, network), say so and stop — don't guess what apply would do.

4. **Summarize and ask.** Before any apply, show the user:
   - the resources to create, change and destroy, one line each;
   - for a rule change, what actually changed in the rule tree. Terraform
     diffs `rules` as one JSON string, which can make untouched rules look
     changed; compare the parsed trees instead:

     ```bash
     terraform show -json tfplan | jq '.resource_changes[] | select(.type == "akamai_property")
       | .change | {before: (.before.rules | fromjson), after: (.after.rules | fromjson)}'
     ```
   - any replacement (`-/+`) or destroy, called out explicitly — replacing an
     existing property or CP code is rarely what the user wants;
   - the activation network (`activation_network`, default `STAGING`), and say
     plainly when it's `PRODUCTION`.

   Then ask whether to apply. Only a clear yes in this conversation, for this
   plan, counts; approval of an earlier plan doesn't carry over to a new one.

5. **Apply the approved plan:** `terraform apply tfplan`, so exactly what the
   user approved is what runs. Activation can take several minutes (longer on
   production); let it finish. Delete `tfplan` afterwards and never commit it —
   plan files can contain sensitive values.

6. **If apply fails, fix and go around again.** PAPI runs rule checks at apply
   that the schema doesn't express — for example an empty required path, or a
   hostname that isn't a valid domain. Read the error's `detail` and
   `location`; the location indexes into the assembled tree, which
   `validate_rules.py --dump rendered.json` writes out. Fix the rule files
   against the fetched page, then rerun from step 2 (validate, Verify, plan)
   and ask again before the next apply. If a fix changes a value the user gave
   you (say `my_destination.com` → `my-destination.com`), tell them. A failed
   apply can leave some resources created; the next apply continues from
   there — don't destroy anything to "clean up" without asking.

Report the outcome as it is: which apply succeeded, what's activated where,
and anything left unresolved.

## Output location

Write to the current project's existing Terraform location if one exists. If no
Terraform directory exists, ask where output should go. Do not assume a path —
this skill runs inside other people's projects.

## Working style

**Stop and ask** — these are the decisions that belong to the user:

- the product name doesn't map to a product ID with certainty;
- several catalog entries plausibly match the request, or none do (Find);
- Verify reports behaviors the product doesn't offer (Verify);
- before every `terraform apply` (Apply);
- a plan would replace or destroy an existing resource (Apply).

Otherwise:

- State assumptions explicitly. If the request is ambiguous, ask before
  generating.
- If a fetched doc and the template disagree, follow the doc and note the
  discrepancy.
- Everything you tell the user about a feature — in option descriptions, plan
  summaries and explanations alike — comes from the catalog, the fetched page,
  or a result you observed (Verify, plan, apply). If you'd be going beyond
  those, say it's unverified or that the docs don't cover it, rather than
  stating it as fact.
- Never invent argument names or option values.
- Surface tradeoffs rather than silently picking one interpretation.

## Maintenance

`references/catalog.tsv` is a snapshot built from techdocs, with its build date
at the top. Rebuild it when a behavior the user names can't be found, or when
it's more than a few months old:

```bash
python scripts/build_catalog.py
```

The build prints page anomalies (wrong or missing samples, unrecognized page
layouts) and drift against the previous catalog; read them before relying on
the new file. Because Verify is live, a stale catalog can only fail to find a
new behavior — it can't make Verify pass a behavior the product doesn't offer.

Dependencies: `pip install requests edgegrid-python jsonschema`.
