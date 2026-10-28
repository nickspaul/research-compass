#!/usr/bin/env python3
"""Read-only checks for this package; not a behavioral or full YAML validator."""

import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REQUIRED = (
    "SKILL.md", "README.md",
    "references/research-principles.md", "references/research-gates.md",
    "references/evidence-standards.md", "references/pace-protocol.md",
    "assets/research-problem-card.md", "assets/novelty-stress-test.md",
    "assets/experiment-precommitment.md", "assets/result-reality-check.md",
    "assets/research-decision-log.md", "tests/README.md", "tests/cases.json",
    "tests/validation-report.md", "tests/file-manifest.txt",
    "references/research-dashboard.md", "assets/research-dashboard.md",
    "tests/dashboard-cases.json", "tests/dashboard-README.md",
    "tests/dashboard-baseline.json", "tests/dashboard-validation-report.md",
)


def validate(root):
    root = root.resolve()
    errors = []

    def check(condition, message):
        if not condition:
            errors.append(message)

    for rel in REQUIRED:
        check((root / rel).is_file(), f"Missing file: {rel}")
    if not (root / "SKILL.md").is_file():
        return errors
    skill = (root / "SKILL.md").read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---(?:\n|$)", skill, re.S)
    check(match is not None, "Missing YAML frontmatter delimiters or UTF-8 BOM")
    if match:
        # Intentionally accepts the exact plain-scalar subset used by this package.
        # Full YAML is checked separately with the creator's validator when available.
        lines = match.group(1).splitlines()
        check(len(lines) == 4, "Expected name, description, metadata and version only")
        if len(lines) == 4:
            check(lines[0].startswith("name: "), "Missing name scalar")
            check(lines[1].startswith("description: "), "Missing description scalar")
            check(lines[2] == "metadata:", "Invalid metadata mapping")
            check(bool(re.fullmatch(r'  version: "\d+\.\d+\.\d+"', lines[3])),
                  "Invalid metadata version")
            name = lines[0].removeprefix("name: ")
            description = lines[1].removeprefix("description: ")
            check(bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)) and len(name) <= 64,
                  "Invalid skill name")
            check(name == root.name, "Name differs from parent directory")
            check(1 <= len(description) <= 1024, "Description length outside specification")
            check(": " not in description and " #" not in description,
                  "Description exceeds this checker's plain-scalar subset")
    check(len(skill.splitlines()) < 500, "Entrypoint exceeds recommended 500 lines")

    markdown_count = 0
    link_count = 0
    for path in sorted(root.rglob("*.md")):
        markdown_count += 1
        is_raw_response = path.parent in {root / "tests/runs", root / "tests/dashboard-runs"} and path.name.endswith("response.md")
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeError as exc:
            errors.append(f"Invalid UTF-8: {path.relative_to(root)}: {exc}")
            continue
        fence = None
        table_width = None
        for number, line in enumerate(text.splitlines(), 1):
            marker = re.match(r"^\s*(`{3,}|~{3,})(.*)$", line)
            if marker:
                if fence is None:
                    fence = marker.group(1)
                elif marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) and not marker.group(2).strip():
                    fence = None
                continue
            if fence:
                continue
            if line.startswith("|") and line.endswith("|"):
                width = len(re.split(r"(?<!\\)\|", line)) - 2
                if table_width is not None:
                    check(width == table_width, f"Uneven table: {path.relative_to(root)}:{number}")
                table_width = width
            else:
                table_width = None
            check(not re.search(r"\[TODO:|\bPLACEHOLDER\b", line),
                  f"Unfinished scaffold: {path.relative_to(root)}:{number}")
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", line):
                if is_raw_response:
                    # Archived observations retain original machine-local provenance.
                    # Only maintained documentation is required to have portable links.
                    continue
                if re.match(r"^[A-Za-z]:", target) or target.startswith("file:"):
                    errors.append(f"Machine-local link: {path.relative_to(root)}:{number}: {target}")
                    continue
                if urlsplit(target).scheme or target.startswith("#"):
                    continue
                link_count += 1
                rel = unquote(target.split("#", 1)[0].strip("<>"))
                check("\\" not in rel and not re.match(r"^[A-Za-z]:", rel) and not rel.startswith("/"),
                      f"Non-portable link: {path.relative_to(root)}:{number}: {target}")
                resolved = (path.parent / rel).resolve()
                check(resolved.is_relative_to(root), f"Link escapes package: {target}")
                check(resolved.exists(), f"Broken link: {path.relative_to(root)}:{number}: {target}")
                if resolved.exists() and resolved.is_relative_to(root):
                    actual = resolved.relative_to(root).as_posix()
                    wanted = Path(path.parent.relative_to(root), rel).as_posix()
                    normalized = []
                    for part in wanted.split("/"):
                        if part == "..":
                            if normalized:
                                normalized.pop()
                        elif part != ".":
                            normalized.append(part)
                    check(actual == "/".join(normalized), f"Link case differs: {target}")
        check(fence is None, f"Unclosed code fence: {path.relative_to(root)}")

    total_cases = 0
    all_ids = set()
    for corpus_name in ("cases.json", "dashboard-cases.json"):
        cases_path = root / "tests" / corpus_name
        if not cases_path.is_file():
            continue
        try:
            corpus = json.loads(cases_path.read_text(encoding="utf-8"))
            check(corpus.get("schema_version") == "1.0", "Unexpected corpus schema_version")
            cases = corpus.get("cases", [])
            check(isinstance(cases, list) and len(cases) >= 10, "Need at least ten test cases")
            total_cases += len(cases)
            seen = set()
            for case in cases:
                ident = case.get("id")
                check(isinstance(ident, str) and bool(ident) and ident not in seen and ident not in all_ids, f"Invalid or duplicate case id: {ident}")
                seen.add(ident)
                all_ids.add(ident)
                for key in ("title", "input", "verification_method"):
                    check(isinstance(case.get(key), str) and bool(case[key].strip()), f"{ident}: missing {key}")
                check(case.get("kind") in {"trigger", "behavior", "integration"}, f"{ident}: invalid kind")
                check(type(case.get("expected_activation")) is bool, f"{ident}: missing activation boolean")
                for key in ("expected_behavior", "failure_behavior"):
                    check(isinstance(case.get(key), list) and bool(case[key]) and
                          all(isinstance(x, str) and x.strip() for x in case[key]), f"{ident}: invalid {key}")
        except (ValueError, TypeError, AttributeError) as exc:
            errors.append(f"Invalid test corpus {corpus_name}: {exc}")

    baseline_path = root / "tests/dashboard-baseline.json"
    if baseline_path.is_file():
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            for rel, expected in baseline["protected_files_sha256"].items():
                protected = (root / rel).resolve()
                check(protected.is_relative_to(root) and protected.is_file(), f"Missing protected source: {rel}")
                if protected.is_relative_to(root) and protected.is_file():
                    check(hashlib.sha256(protected.read_bytes()).hexdigest() == expected,
                          f"Dashboard changed protected source: {rel}")
            original_body = re.sub(r"\A---\n.*?\n---\n", "", skill, count=1, flags=re.S)
            start = original_body.find(baseline["added_section_heading"] + "\n")
            end = original_body.find(baseline["following_original_heading"] + "\n", start)
            check(start >= 0 and end > start, "Dashboard section boundary missing")
            if start >= 0 and end > start:
                restored = original_body[:start] + original_body[end:]
                check(hashlib.sha256(restored.encode("utf-8")).hexdigest() == baseline["original_skill_body_sha256"],
                      "Dashboard changed original Skill body outside the added section")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            errors.append(f"Invalid dashboard preservation baseline: {exc}")

    if not errors:
        print(f"PASS: {len(REQUIRED)} required resources; {markdown_count} Markdown files; {link_count} local links; {total_cases} cases")
        print("PASS: protected core files and original Skill body preserved outside dashboard addition.")
        print("Scope: package structure and corpus only; no model behavior or host discovery asserted.")
    return errors


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python validate.py <research-compass-folder>", file=sys.stderr)
        sys.exit(2)
    issues = validate(Path(sys.argv[1]))
    for issue in issues:
        print(f"FAIL: {issue}", file=sys.stderr)
    sys.exit(bool(issues))
