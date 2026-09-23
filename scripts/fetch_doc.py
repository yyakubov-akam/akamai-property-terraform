#!/usr/bin/env python3
"""Fetch an Akamai techdocs page as markdown, by URL or by name.

Resolving by name uses the catalog (references/catalog.tsv) rather than
constructing a URL, because slugs are not reliably derived from identifiers
(edge_worker -> edgeworker.md). A name matches a row's Terraform identifier,
its PAPI name, or its UI display name:
    origin_ip_acl  /  originIpAcl  /  "Origin IP Access Control List"

Usage:
    python scripts/fetch_doc.py https://techdocs.akamai.com/terraform/docs/origin-ip-acl.md
    python scripts/fetch_doc.py --name origin_ip_acl
    python scripts/fetch_doc.py --name "HTTPS Cache Key Sharing"   # display name
    python scripts/fetch_doc.py --name include --kind resource     # disambiguate by kind
    python scripts/fetch_doc.py --name origin --list               # show matches, do not fetch
"""

import argparse
import pathlib
import re
import sys
import unicodedata

try:
    import requests
except ImportError:
    sys.exit("requests is not installed. Try: pip install requests")

CATALOG_PATH = pathlib.Path(__file__).resolve().parent.parent / "references" / "catalog.tsv"
COLUMNS = ["kind", "identifier", "papi_name", "display_name", "version", "includes", "url", "note", "description"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/plain,text/markdown,*/*",
}

# Zero-width and other invisible characters that appear inside identifiers in
# the published index and silently break exact matching.
INVISIBLE = dict.fromkeys(
    [
        0x200B,  # zero-width space
        0x200C,  # zero-width non-joiner
        0x200D,  # zero-width joiner
        0x2060,  # word joiner
        0xFEFF,  # zero-width no-break space
        0x00AD,  # soft hyphen
    ]
)


def clean(text: str) -> str:
    """Strip invisible characters and normalise, so matching behaves predictably."""
    text = unicodedata.normalize("NFKC", text)
    return text.translate(INVISIBLE)


def normalise_key(text: str) -> str:
    """Reduce a name to comparable form: lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]+", "", clean(text).lower())


def load_catalog() -> list[dict]:
    if not CATALOG_PATH.exists():
        sys.exit(f"No catalog at {CATALOG_PATH}\nRun: python scripts/build_catalog.py")

    rows = []
    for line in CATALOG_PATH.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            rows.append(dict(zip(COLUMNS, line.split("\t"))))
    if not rows:
        sys.exit(f"Catalog at {CATALOG_PATH} contains no entries. Re-run build_catalog.py")
    return rows


def names(row: dict) -> list[str]:
    """The normalised names a row answers to."""
    return [normalise_key(row[c]) for c in ("identifier", "papi_name", "display_name") if row[c] != "-"]


def resolve(name: str, rows: list[dict]) -> list[dict]:
    """Return matching rows, best-effort, in decreasing specificity."""
    key = normalise_key(name)

    exact = [r for r in rows if key in names(r)]
    if exact:
        return exact

    substring = [r for r in rows if any(key in n for n in names(r))]
    if substring:
        return substring

    # Token-based fallback: every word in the query appears in one of the names.
    tokens = [t for t in re.split(r"[^a-z0-9]+", clean(name).lower()) if t]
    if tokens:
        return [r for r in rows if any(all(t in n for t in tokens) for n in names(r))]
    return []


def warn_about_samples(row: dict) -> None:
    """Pages whose samples can't be copied as-is (see catalog.tsv's note column)."""
    note = row["note"]
    if note.startswith("wrong-sample:"):
        print(f"# WARNING: this page's samples are for {note.split(':', 1)[1]!r}, not "
              f"{row['identifier']!r}. Do not copy them or cite their values as defaults; take "
              "option names and values from the Options table and confirm with validate_rules.py.",
              file=sys.stderr)
    elif note == "no-sample":
        print(f"# WARNING: this page has no JSON sample, so its PAPI name is unconfirmed. "
              "Build the behavior from the Options table and confirm with validate_rules.py.",
              file=sys.stderr)


def fetch(url: str, timeout: int = 30) -> str:
    if not url.endswith(".md"):
        url = url.rstrip("/") + ".md"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        sys.exit(f"Network error fetching {url}: {exc}")

    if resp.status_code == 403:
        sys.exit(
            f"403 Forbidden fetching {url}\n"
            "Bot protection likely blocked this request. Do NOT generate the "
            "behavior from memory - report the failed lookup instead."
        )
    if resp.status_code == 404:
        sys.exit(
            f"404 Not Found: {url}\n"
            "If this URL was constructed rather than taken from the index, "
            "resolve it via --name instead. If it came from the index, the "
            "catalog may be stale - run build_catalog.py."
        )
    if resp.status_code != 200:
        sys.exit(f"HTTP {resp.status_code} fetching {url}")

    return resp.text


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url", nargs="?", help="Documentation URL to fetch")
    parser.add_argument("--name", help="identifier, PAPI name, or display name to resolve via the catalog")
    parser.add_argument("--kind", choices=["behavior", "criterion", "resource", "data_source", "guide"],
                        help="only match rows of this kind")
    parser.add_argument("--list", action="store_true", help="Show matches without fetching")
    args = parser.parse_args()

    if not args.url and not args.name:
        parser.error("provide a URL or --name")

    if args.url:
        sys.stdout.write(fetch(args.url))
        return

    rows = load_catalog()
    if args.kind:
        rows = [r for r in rows if r["kind"] == args.kind]
    matches = resolve(args.name, rows)

    if not matches:
        sys.exit(
            f"No catalog entry matches {args.name!r} by name.\n"
            "Grep the catalog's descriptions instead (grep -i '<fragment>' references/catalog.tsv),\n"
            "or rebuild it if the behavior is new: python scripts/build_catalog.py"
        )

    if args.list or len(matches) > 1:
        print(f"{len(matches)} match(es) for {args.name!r}:", file=sys.stderr)
        for r in matches:
            print(f"  {r['kind']}\t{r['identifier']}\t{r['display_name']}\t{r['url']}", file=sys.stderr)
        if len(matches) > 1:
            sys.exit(
                "\nAmbiguous. Ask the user which one they mean, then fetch by URL.\n"
                "Adjacent behaviors do very different things - do not pick silently."
            )
        return

    row = matches[0]
    print(f"# resolved: {row['kind']} {row['identifier']} (PAPI: {row['papi_name']}) -> {row['url']}",
          file=sys.stderr)
    warn_about_samples(row)
    sys.stdout.write(fetch(row["url"]))


if __name__ == "__main__":
    main()
