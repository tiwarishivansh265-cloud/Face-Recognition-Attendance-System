"""Dependency-free static checks for the FRAMS Flask project."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app.py"
TEMPLATES = ROOT / "templates"


def main():
    if not APP.is_file():
        raise SystemExit("FAIL: app.py is missing")
    source = APP.read_text(encoding="utf-8")
    functions = set(re.findall(r"^def\s+(\w+)\s*\(", source, re.MULTILINE))
    rendered = set(re.findall(r"render_template\(\s*['\"]([^'\"]+)", source))
    missing_templates = sorted(name for name in rendered if not (TEMPLATES / name).is_file())
    if missing_templates:
        print("FAIL: render_template references missing templates:")
        for name in missing_templates:
            print(f"  - templates/{name}")
        return 1

    endpoints = set(re.findall(r"url_for\(\s*['\"]([^'\"]+)", "\n".join(
        p.read_text(encoding="utf-8") for p in TEMPLATES.glob("*.html")
    )))
    builtins = {"static"}
    missing_endpoints = sorted(name for name in endpoints if name not in functions and name not in builtins)
    if missing_endpoints:
        print("FAIL: templates reference unknown Flask endpoints:")
        for name in missing_endpoints:
            print(f"  - {name}")
        return 1

    print(f"PASS: app.py found; {len(rendered)} rendered templates exist.")
    print(f"PASS: {len(endpoints)} template URL endpoints resolve to app functions or Flask built-ins.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
