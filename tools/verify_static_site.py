#!/usr/bin/env python3
"""Read-only checks for local static-site assets and Pages deployment inputs."""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit


DEPLOYED_ASSETS = frozenset({"index.html", "style.css", "script.js"})
WORKFLOW_PATH = Path(".github/workflows/deploy-cloudflare-pages.yml")


class AssetReferenceParser(HTMLParser):
    """Collect local stylesheet and script URLs without fetching any URL."""

    def __init__(self) -> None:
        super().__init__()
        self.references: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "link" and "stylesheet" in (values.get("rel") or "").lower().split():
            if values.get("href"):
                self.references.append(values["href"])
        elif tag == "script" and values.get("src"):
            self.references.append(values["src"])


def local_asset_path(root: Path, reference: str) -> Path | None:
    """Return a root-contained local path, excluding external and anchor URLs."""
    parsed = urlsplit(reference)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    root = root.resolve()
    candidate = (root / unquote(parsed.path.lstrip("/"))).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def verify_html_references(root: Path) -> list[str]:
    index = root / "index.html"
    if not index.is_file():
        return ["missing index.html"]
    parser = AssetReferenceParser()
    parser.feed(index.read_text(encoding="utf-8"))
    parser.close()
    errors = []
    for reference in parser.references:
        asset = local_asset_path(root, reference)
        if asset is not None and not asset.is_file():
            errors.append(f"index.html references missing local asset: {reference}")
    return errors


def deployment_assets(workflow: Path) -> tuple[set[str], set[str], list[str]]:
    """Read the workflow's explicit dist copy and expected-file rules."""
    if not workflow.is_file():
        return set(), set(), [f"missing workflow: {workflow}"]
    text = workflow.read_text(encoding="utf-8")
    copies = set(re.findall(r"(?m)^\s*cp\s+([A-Za-z0-9_.-]+)\s+dist/([A-Za-z0-9_.-]+)\s*$", text))
    copied = {target for source, target in copies if source == target}
    errors = [f"workflow copy must preserve filename: {source} -> {target}" for source, target in copies if source != target]
    expected_match = re.search(r"printf '%s\\n' ([A-Za-z0-9_. -]+) \| sort > \"\$\{RUNNER_TEMP\}/expected-files\.txt\"", text)
    expected = set(expected_match.group(1).split()) if expected_match else set()
    if not expected_match:
        errors.append("workflow has no parseable expected deployment file list")
    return copied, expected, errors


def unallowlisted_dist_operations(workflow_text: str) -> list[str]:
    """Return literal dist operations that are not part of the fixed workflow.

    The verifier intentionally accepts only the small set of read operations and
    copies used by this repository.  This catches alternate shell syntax that
    could add a file after the expected-file list has been checked.
    """
    allowed_copies = {
        f"cp {asset} dist/{asset}" for asset in DEPLOYED_ASSETS
    }
    errors = []
    dist_reference = re.compile(r"(?<![A-Za-z0-9_.-])(?:\./)?dist(?:/|\b)")
    for line_number, raw_line in enumerate(workflow_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or not dist_reference.search(line):
            continue
        is_allowed = (
            line in allowed_copies
            or line == "mkdir dist"
            or re.fullmatch(r"test(?:\s+(?:!|-e|-f|dist(?:/[A-Za-z0-9_.-]+)?))+", line) is not None
            or re.fullmatch(
                r"find dist -type f -printf '%P\\n' \| sort > \"\$\{RUNNER_TEMP\}/actual-files\.txt\"",
                line,
            ) is not None
            or line in {
                "grep -Fq 'href=\"style.css\"' dist/index.html",
                "grep -Fq 'src=\"script.js\"' dist/index.html",
            }
            or re.fullmatch(r"command:\s+pages deploy dist(?:\s+[-A-Za-z0-9_./=]+)*", line) is not None
        )
        if not is_allowed:
            errors.append(f"workflow has unallowlisted dist operation on line {line_number}")
    return errors


def verify_deployment_allowlist(root: Path) -> list[str]:
    workflow = root / WORKFLOW_PATH
    copied, expected, errors = deployment_assets(workflow)
    if workflow.is_file():
        errors.extend(unallowlisted_dist_operations(workflow.read_text(encoding="utf-8")))
    if copied != DEPLOYED_ASSETS:
        errors.append(f"workflow deploy copy set must be {sorted(DEPLOYED_ASSETS)}, found {sorted(copied)}")
    if expected != DEPLOYED_ASSETS:
        errors.append(f"workflow expected file set must be {sorted(DEPLOYED_ASSETS)}, found {sorted(expected)}")
    if copied != expected:
        errors.append("workflow copied and expected deployment file sets differ")
    if not workflow.is_file() or not re.search(r"(?m)^\s*command:\s+pages deploy dist(?:\s|$)", workflow.read_text(encoding="utf-8")):
        errors.append("workflow does not deploy the verified dist directory")
    return errors


def verify(root: Path) -> list[str]:
    root = root.resolve()
    return [*verify_html_references(root), *verify_deployment_allowlist(root)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    errors = verify(args.root)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print("PASS: local HTML assets and Cloudflare Pages deployment allowlist are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
