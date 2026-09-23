#!/usr/bin/env python3
"""Build references/catalog.tsv: one line per techdocs page, with a real
description, so an agent can pick which page to fetch by meaning rather than
by guessing from a bare identifier.

Everything comes from techdocs itself. The property llms.txt supplies the
page list; each page's own header supplies the rest:

    behavior / criterion   "**Behavior name:** Cloud Monitor Instrumentation"
                           + description paragraph, rule format version,
                           includes flag, and a JSON sample whose "name" is
                           the exact PAPI identifier (e.g. allowHTTPSCacheKeySharing)
    resource / data_source H1 title, a bare akamai_* type line, description;
                           the first code sample says `resource` or `data`
    guide                  anything else: H1 title + first prose paragraph

The kind is read from the page, never inferred from the URL slug.

Usage:
    python scripts/build_catalog.py
    python scripts/build_catalog.py --cache-dir /tmp/techdocs   # reuse fetched pages
    python scripts/build_catalog.py --allow-partial              # write despite fetch failures
"""

import argparse
import concurrent.futures
import datetime as dt
import html
import pathlib
import re
import sys

try:
    import requests
except ImportError:
    sys.exit("requests is not installed. Try: pip install requests")

from fetch_doc import COLUMNS, HEADERS, clean, normalise_key

DEFAULT_URL = "https://techdocs.akamai.com/terraform/docs/property/llms.txt"

CATALOG_PATH = pathlib.Path(__file__).resolve().parent.parent / "references" / "catalog.tsv"

# COLUMNS is imported from fetch_doc.py, which reads the catalog back.
# note values, for pages whose samples can't be trusted as-is:
#   no-sample          the page has no JSON sample, so papi_name is unknown
#   wrong-sample:<x>   the page's samples are for behavior <x>, not this one
KIND_ORDER = ["behavior", "criterion", "resource", "data_source", "guide"]
EMPTY = "-"

ENTRY_RE = re.compile(r"^\s*-\s*\[(?P<title>[^\]]+)\]\((?P<url>[^)]+)\)")
# Techdocs is inconsistent here: "Criteria name", "Behavior Name" both occur.
LABEL_RE = re.compile(r"^\*\*(Behavior|Criterion|Criteria) name:\*\*\s*(.+)$", re.M | re.I)
VERSION_RE = re.compile(r"Version:\s*<code>(v[\d-]+)</code>")
INCLUDES_RE = re.compile(r"Includes use:\s*(Yes|No)")
TF_TYPE_RE = re.compile(r"^akamai_[a-z0-9_]+$")
JSON_BLOCK_RE = re.compile(r"^```json JSON\s*$(.*?)^```", re.M | re.S)
# Case-insensitive: at least one page (allow-delete) has "Name" in its sample.
JSON_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]+)"', re.I)
FENCE_RE = re.compile(r"^```[^\n]*\n(.*?)^```", re.M | re.S)
# First declaration in a sample; samples may open with // comment lines.
DECLARATION_RE = re.compile(r"^(resource|data)\s+\"", re.M)
BARE_VERSION_RE = re.compile(r"^v\d{4}-\d{2}-\d{2}$")
# Block-level HTML only: many descriptions legitimately open with an inline <a> link.
HTML_BLOCK_RE = re.compile(r"^<(table|thead|div|img|p|span|br)\b", re.I)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def fetch(url: str, timeout: int = 30) -> str:
    """Fetch a URL as text. Raises on failure so callers decide how loud to be."""
    resp = requests.get(url, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    return resp.text


def fetch_page(url: str, cache_dir: pathlib.Path | None) -> str:
    if not url.endswith(".md"):
        url = url.rstrip("/") + ".md"
    cached = cache_dir / url.rsplit("/", 1)[-1] if cache_dir else None
    if cached and cached.exists():
        return cached.read_text(encoding="utf-8")
    body = fetch(url)
    if cached:
        cached.write_text(body, encoding="utf-8")
    return body


def parse_index(body: str) -> list[tuple[str, str]]:
    """(title, url) pairs from llms.txt, deduplicated by URL, order preserved."""
    seen, entries = set(), []
    for line in body.splitlines():
        match = ENTRY_RE.match(clean(line))
        if match and match.group("url") not in seen:
            seen.add(match.group("url"))
            entries.append((match.group("title").strip(), match.group("url").strip()))
    return entries


# ---------------------------------------------------------------------------
# Page parsing
# ---------------------------------------------------------------------------

def plain_text(text: str) -> str:
    """Reduce a markdown/HTML fragment to one line of plain text."""
    text = re.sub(r"<code>(.*?)</code>", r"`\1`", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", r"\1", text)  # markdown escapes: \_ -> _
    text = html.unescape(clean(text))
    return re.sub(r"\s+", " ", text).strip()


def first_paragraph(lines: list[str]) -> str:
    """First prose paragraph in `lines`, up to the next heading or code sample.

    Skips HTML blocks (tables, the "processing time" banner), notes, bare
    version stamps and the akamai_* type line that open some pages.
    """
    paragraph: list[str] = []
    for line in lines + [""]:
        stripped = line.strip()
        if stripped.startswith(("#", "```")):
            break
        if stripped:
            paragraph.append(stripped)
            continue
        if paragraph:
            first = paragraph[0]
            text = plain_text(" ".join(paragraph))
            skip = (HTML_BLOCK_RE.match(first) or first.startswith(("|", ">"))
                    or BARE_VERSION_RE.match(first) or TF_TYPE_RE.match(first))
            if text and not skip:
                return text
            paragraph = []
    return ""


def summarize(paragraph: str) -> str:
    """Trim a page's first paragraph to what goes in the catalog's description column.

    Currently keeps the whole paragraph (~57KB across the catalog): first
    sentences alone can mislead (ad_scaler_circuit_breaker's describes a
    dependency, not the behavior) and drop notes like "enable http2 first".
    """
    return paragraph


def after(lines: list[str], predicate) -> list[str]:
    """The lines following the first line that satisfies `predicate`."""
    for i, line in enumerate(lines):
        if predicate(line):
            return lines[i + 1:]
    return []


def parse_page(body: str, url: str) -> dict:
    """Classify a page and pull out the catalog fields. Missing fields stay EMPTY."""
    body = clean(body)
    lines = body.splitlines()
    h1 = next((l[2:].strip() for l in lines if l.startswith("# ")), "")
    row = dict.fromkeys(COLUMNS, EMPTY)
    row["url"] = url

    label = LABEL_RE.search(body)
    if label:
        row["kind"] = "behavior" if label.group(1).lower() == "behavior" else "criterion"
        row["identifier"] = h1
        row["display_name"] = plain_text(label.group(2))
        row["description"] = first_paragraph(after(lines, lambda l: LABEL_RE.match(l)))
        if m := VERSION_RE.search(body):
            row["version"] = m.group(1)
        if m := INCLUDES_RE.search(body):
            row["includes"] = m.group(1).lower()
        if (block := JSON_BLOCK_RE.search(body)) and (m := JSON_NAME_RE.search(block.group(1))):
            # Some pages carry another behavior's sample (first_party_marketing
            # and _plus are swapped). Acronyms survive normalising (gRPC ==
            # g_rpc), so a mismatch here means the sample is wrong, not the name.
            if normalise_key(m.group(1)) == normalise_key(h1):
                row["papi_name"] = m.group(1)
            else:
                row["note"] = f"wrong-sample:{m.group(1)}"
        else:
            row["note"] = "no-sample"
        return row

    below_h1 = after(lines, lambda l: l.startswith("# "))
    tf_type = next((l.strip() for l in below_h1 if l.strip()), "")
    if TF_TYPE_RE.match(tf_type):
        first_code = FENCE_RE.search(body)
        declaration = DECLARATION_RE.search(first_code.group(1)) if first_code else None
        row["kind"] = {"resource": "resource", "data": "data_source"}.get(
            declaration.group(1) if declaration else "", "unknown")
        row["identifier"] = tf_type
    else:
        row["kind"] = "guide"
        row["identifier"] = url.rsplit("/", 1)[-1].removesuffix(".md")
    row["display_name"] = plain_text(h1)
    row["description"] = first_paragraph(below_h1)
    return row


# ---------------------------------------------------------------------------
# Output and drift report
# ---------------------------------------------------------------------------

def load_previous(path: pathlib.Path) -> dict[tuple[str, str], dict]:
    if not path.exists():
        return {}
    rows = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        row = dict(zip(COLUMNS, line.split("\t")))
        rows[(row["kind"], row["identifier"])] = row
    return rows


def anomalies(rows: list[dict]) -> list[str]:
    """Rows whose page didn't match the expected header shape for its kind."""
    problems = []
    for row in rows:
        where = f"{row['kind']} {row['identifier']} ({row['url']})"
        if row["kind"] == "unknown":
            problems.append(f"unclassified page: {where}")
        if row["note"] != EMPTY:
            problems.append(f"{row['note']}: {where}")
        if row["kind"] in ("behavior", "criterion"):
            for field in ("version", "includes"):
                if row[field] == EMPTY:
                    problems.append(f"missing {field}: {where}")
        # Guides may legitimately open with a table or heading instead of prose.
        if row["kind"] != "guide" and row["description"] in (EMPTY, ""):
            problems.append(f"missing description: {where}")
    return problems


def drift(previous: dict, rows: list[dict]) -> list[str]:
    current = {(r["kind"], r["identifier"]): r for r in rows}
    lines = [f"added:   {k[0]} {k[1]}" for k in sorted(current.keys() - previous.keys())]
    lines += [f"removed: {k[0]} {k[1]}" for k in sorted(previous.keys() - current.keys())]
    for key in sorted(current.keys() & previous.keys()):
        for field in ("papi_name", "version", "url"):
            if current[key][field] != previous[key][field]:
                lines.append(f"changed: {key[0]} {key[1]} {field}: "
                             f"{previous[key][field]} -> {current[key][field]}")
    return lines


def render(rows: list[dict], source: str) -> str:
    counts = ", ".join(f"{k} {sum(r['kind'] == k for r in rows)}"
                       for k in KIND_ORDER + ["unknown"] if any(r["kind"] == k for r in rows))
    header = (
        f"# Akamai Terraform property catalog, built from techdocs pages\n"
        f"# source:  {source}\n"
        f"# built:   {dt.date.today().isoformat()}\n"
        f"# entries: {len(rows)} ({counts})\n"
        f"#\n"
        f"# One line per techdocs page, tab-separated. Naming/discovery only - fetch\n"
        f"# the page itself for syntax. A note of wrong-sample:<x> means that page's\n"
        f"# samples are for <x>; do not copy them. Rebuild with:\n"
        f"#   python scripts/build_catalog.py\n"
        f"#\n"
        f"# {chr(9).join(COLUMNS)}\n"
    )
    body = "\n".join("\t".join(r[c].replace("\t", " ") for c in COLUMNS) for r in rows)
    return header + body + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_URL, help="llms.txt URL listing the pages")
    parser.add_argument("--cache-dir", type=pathlib.Path, help="read/write raw pages here to avoid refetching")
    parser.add_argument("--allow-partial", action="store_true", help="write the catalog even if some pages failed")
    parser.add_argument("--workers", type=int, default=4, help="concurrent page fetches (default 4)")
    args = parser.parse_args()

    try:
        entries = parse_index(fetch(args.url))
    except requests.RequestException as exc:
        sys.exit(f"Could not fetch the page list {args.url}: {exc}")
    if not entries:
        sys.exit(f"No entries found in {args.url} - format may have changed. Not writing.")
    if args.cache_dir:
        args.cache_dir.mkdir(parents=True, exist_ok=True)

    rows, failures = [], []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch_page, url, args.cache_dir): url for _, url in entries}
        for future in concurrent.futures.as_completed(futures):
            url = futures[future]
            try:
                row = parse_page(future.result(), url)
            except requests.RequestException as exc:
                failures.append(f"{url}: {exc}")
                continue
            row["description"] = summarize(row["description"]) or EMPTY
            rows.append(row)

    rank = {k: i for i, k in enumerate(KIND_ORDER)}
    rows.sort(key=lambda r: (rank.get(r["kind"], len(rank)), r["identifier"]))

    previous = load_previous(CATALOG_PATH)
    report = [("fetch failures", failures), ("page anomalies", anomalies(rows)),
              ("drift vs previous catalog", drift(previous, rows) if previous else [])]
    for title, items in report:
        print(f"\n{title}: {len(items)}", file=sys.stderr)
        for item in items:
            print(f"  {item}", file=sys.stderr)

    if failures and not args.allow_partial:
        sys.exit("\nSome pages failed to fetch. Not overwriting the catalog (use --allow-partial).")

    CATALOG_PATH.write_text(render(rows, args.url), encoding="utf-8")
    print(f"\nWrote {len(rows)} entries to {CATALOG_PATH}", file=sys.stderr)


if __name__ == "__main__":
    main()
