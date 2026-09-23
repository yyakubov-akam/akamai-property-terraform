#!/usr/bin/env python3
"""Validate a property rule tree against PAPI's rule schema for a product,
before `terraform apply` does it the expensive way.

PAPI validates a rule tree against a product-specific JSON schema, and the
provider only submits the tree at apply time - after the property and its
first version already exist. This script runs the same check up front:

  1. assemble the tree the way akamai_property_rules_template does:
     "#include:<path>" strings are replaced by that file's JSON (paths
     resolve from the top-level template's directory), and "${env.NAME}"
     values are replaced by --var values;
  2. fetch the product's schema: GET /papi/v1/schemas/products/{product}/{ruleFormat};
  3. report behaviors/criteria the product doesn't offer, then any other
     schema errors, each with the rule path it occurs in.

Usage:
    python scripts/validate_rules.py rules/main.json --product prd_Site_Accel \\
        --var cp_code_id=12345 --var cp_code_name=placeholder
    python scripts/validate_rules.py rules/main.json --product prd_SPM --rule-format v2026-06-09
    python scripts/validate_rules.py rules/main.json --product prd_SPM --dump rendered.json

--var values are parsed as JSON when possible (12345 -> number, true -> bool),
otherwise kept as strings - the same typed substitution the provider does.
Use placeholders whose type matches the `variables { type = ... }` blocks in
the Terraform config; the real values usually aren't known before apply.

Exit status: 0 valid, 1 invalid, 2 could not validate (usage, files, API).
"""

import argparse
import json
import os
import pathlib
import re
import sys

try:
    import requests
    from akamai.edgegrid import EdgeGridAuth, EdgeRc
    from jsonschema import Draft4Validator
except ImportError as exc:
    sys.exit(f"Missing dependency ({exc.name}). Try: pip install requests edgegrid-python jsonschema")

INCLUDE_PREFIX = "#include:"
ENV_RE = re.compile(r"\$\{env\.([A-Za-z0-9_]+)\}")


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(2)


# ---------------------------------------------------------------------------
# Assembling the tree
# ---------------------------------------------------------------------------

def parse_vars(pairs: list[str]) -> dict:
    values = {}
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep:
            fail(f"--var expects NAME=VALUE, got {pair!r}")
        try:
            values[name] = json.loads(raw)
        except json.JSONDecodeError:
            values[name] = raw
    return values


def assemble(node, root_dir: pathlib.Path, variables: dict, missing: set, seen: tuple = ()):
    """Recursively resolve includes and substitute variables."""
    if isinstance(node, dict):
        return {k: assemble(v, root_dir, variables, missing, seen) for k, v in node.items()}
    if isinstance(node, list):
        return [assemble(v, root_dir, variables, missing, seen) for v in node]
    if not isinstance(node, str):
        return node

    if node.startswith(INCLUDE_PREFIX):
        path = (root_dir / node[len(INCLUDE_PREFIX):].strip()).resolve()
        if path in seen:
            fail(f"Include cycle: {' -> '.join(str(p) for p in seen + (path,))}")
        try:
            included = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(f"Cannot load include {node!r} ({path}): {exc}")
        return assemble(included, root_dir, variables, missing, seen + (path,))

    whole = ENV_RE.fullmatch(node)
    if whole:  # a value that is only "${env.x}" takes the variable's own type
        name = whole.group(1)
        if name not in variables:
            missing.add(name)
            return node
        return variables[name]

    def substitute(match):
        if match.group(1) not in variables:
            missing.add(match.group(1))
            return match.group(0)
        return str(variables[match.group(1)])

    return ENV_RE.sub(substitute, node)


# ---------------------------------------------------------------------------
# Fetching the schema
# ---------------------------------------------------------------------------

def fetch_schema(product: str, rule_format: str, edgerc: str, section: str, switch_key: str | None) -> dict:
    try:
        rc = EdgeRc(os.path.expanduser(edgerc))
        host = rc.get(section, "host")
        session = requests.Session()
        session.auth = EdgeGridAuth.from_edgerc(rc, section)
    except Exception as exc:  # EdgeRc raises configparser errors of several kinds
        fail(f"Cannot read credentials from {edgerc} [{section}]: {exc}")

    params = {"accountSwitchKey": switch_key} if switch_key else {}
    url = f"https://{host}/papi/v1/schemas/products/{product}/{rule_format}"
    try:
        resp = session.get(url, params=params, headers={"Accept": "application/json"}, timeout=60)
    except requests.RequestException as exc:
        fail(f"Network error fetching the rule schema: {exc}")
    if resp.status_code != 200:
        fail(f"HTTP {resp.status_code} fetching the {product} / {rule_format} schema:\n{resp.text[:1000]}\n"
             "Check the product ID (e.g. prd_SPM, prd_Site_Accel) and rule format.")
    return resp.json()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def rule_path(tree: dict, pointer: list) -> str:
    """Turn a JSON path into rule names: 'default > Accelerate delivery > ...'."""
    names, node = [], tree
    for i, key in enumerate(pointer):
        node = node[key]
        if isinstance(node, dict) and "name" in node and (i == 0 or pointer[i - 1] == "children"):
            names.append(node["name"])
    return " > ".join(names)


def unavailable(tree: dict, schema: dict) -> list[tuple[str, str]]:
    """(message, JSON pointer) for behaviors/criteria this product's schema doesn't offer."""
    catalog = schema.get("definitions", {}).get("catalog", {})
    offered = {"behaviors": set(catalog.get("behaviors", {})), "criteria": set(catalog.get("criteria", {}))}
    found = []

    def walk(rule: dict, path: list):
        for key in ("behaviors", "criteria"):
            for i, item in enumerate(rule.get(key, [])):
                name = item.get("name") if isinstance(item, dict) else None
                if name and name not in offered[key]:
                    kind = "behavior" if key == "behaviors" else "criterion"
                    pointer = "/" + "/".join(str(p) for p in path + [key, i])
                    found.append((f"{kind} {name!r} in rule: {rule_path(tree, path) or '(top)'}", pointer))
        for i, child in enumerate(rule.get("children", [])):
            if isinstance(child, dict):
                walk(child, path + ["children", i])

    walk(tree["rules"], ["rules"])
    return found


def matching_leaves(error) -> list:
    """The errors worth reporting from one (possibly anyOf) validation error.

    Behaviors and criteria are anyOf[one branch per name], so a bad option
    yields an error from every branch - most just saying the name doesn't
    match that branch. Keep only branches whose `name` did match, recursing
    into nested anyOf/oneOf the same way.
    """
    if not error.context:
        return [error]
    branches: dict = {}
    for sub in error.context:
        branches.setdefault(sub.schema_path[0], []).append(sub)
    matched = [subs for subs in branches.values() if not any(list(s.relative_path) == ["name"] for s in subs)]
    if not matched:  # no branch matched at all; the error itself is the best we have
        return [error]
    return [leaf for subs in matched for sub in subs for leaf in matching_leaves(sub)]


def schema_errors(tree: dict, schema: dict, skip: list[str]) -> list[str]:
    """All other schema violations, most specific message per location.

    Errors under an already-reported unavailable behavior are skipped: once
    its name fails the enum, its options get checked against the wrong
    definition and produce misleading noise.
    """
    messages = {}
    for error in Draft4Validator(schema).iter_errors(tree):
        for leaf in matching_leaves(error):
            location = list(leaf.absolute_path)
            pointer = "/" + "/".join(str(p) for p in location)
            if any(pointer == s or pointer.startswith(s + "/") for s in skip):
                continue
            messages.setdefault(pointer, f"{pointer}: {leaf.message}  [rule: {rule_path(tree, location) or '(top)'}]")
    return list(messages.values())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("template", type=pathlib.Path, help="top-level rule template, e.g. rules/main.json")
    parser.add_argument("--product", required=True, help="product ID, e.g. prd_SPM, prd_Site_Accel")
    parser.add_argument("--rule-format", default="latest", help="rule format (default: latest, as the template uses)")
    parser.add_argument("--var", action="append", default=[], metavar="NAME=VALUE", help="template variable value")
    parser.add_argument("--edgerc", default="~/.edgerc")
    parser.add_argument("--section", default="default", help=".edgerc section (default: default)")
    parser.add_argument("--account-switch-key", help="for accounts reached through an account switch key")
    parser.add_argument("--schema-file", type=pathlib.Path, help="use a saved schema instead of calling PAPI")
    parser.add_argument("--dump", type=pathlib.Path, help="also write the assembled rule tree here")
    args = parser.parse_args()

    try:
        top = json.loads(args.template.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"Cannot load {args.template}: {exc}")
    missing: set = set()
    tree = assemble(top, args.template.resolve().parent, parse_vars(args.var), missing)
    if missing:
        fail("Unresolved template variables: " + ", ".join(sorted(missing)) + "\n"
             "Pass placeholders of the right type, e.g. " +
             " ".join(f"--var {n}=<value>" for n in sorted(missing)))
    if args.dump:
        args.dump.write_text(json.dumps(tree, indent=2) + "\n", encoding="utf-8")

    if args.schema_file:
        schema = json.loads(args.schema_file.read_text(encoding="utf-8"))
    else:
        schema = fetch_schema(args.product, args.rule_format, args.edgerc, args.section, args.account_switch_key)

    missing_names = unavailable(tree, schema)
    other = schema_errors(tree, schema, [pointer for _, pointer in missing_names])
    label = f"{args.product} / {args.rule_format}"
    if not missing_names and not other:
        print(f"OK: rule tree is valid for {label}")
        return

    if missing_names:
        print(f"Not available for {label} ({len(missing_names)}):")
        for line, _ in missing_names:
            print(f"  {line}")
        print("  -> Ask the user: remove these, substitute an available behavior, or change the product.")
    if other:
        print(f"Other schema errors ({len(other)}):")
        for line in other:
            print(f"  {line}")
    sys.exit(1)


if __name__ == "__main__":
    main()
