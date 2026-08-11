#!/usr/bin/env python3
"""Assemble the inner system-prompt stack from its ordered layer files.

The layers live as `NN-name.md` files and are concatenated in numeric order into
`inner-system-prompt.md`. The single file is a *generated artifact* — edit the
layers, not the output.

Usage:
    python3 assemble.py            # (re)generate inner-system-prompt.md
    python3 assemble.py --check    # verify the output is current; exit 1 if not

The --check mode is the self-measurement loop: CI (or a pre-commit hook, or an
agent) can run it to prove the committed prompt still matches its sources.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "inner-system-prompt.md"
LAYER_RE = re.compile(r"^\d{2}-.+\.md$")

HEADER = (
    "<!-- GENERATED FILE — do not edit by hand.\n"
    "     Source: docs/system-prompt-stack/NN-*.md  |  Regenerate: python3 assemble.py\n"
    "     Layers are ordered slowest-changing (top) to fastest-changing (bottom):\n"
    "     the conserved core sits ABOVE the self-improvement engine, on purpose. -->\n"
)


def layer_files() -> list[Path]:
    files = sorted(p for p in HERE.iterdir() if LAYER_RE.match(p.name))
    if not files:
        raise SystemExit("no layer files (NN-*.md) found next to assemble.py")
    return files


def build() -> str:
    parts = [HEADER]
    for f in layer_files():
        body = f.read_text(encoding="utf-8").strip()
        if not body:
            raise SystemExit(f"layer {f.name} is empty — every layer must carry content")
        parts.append(body)
    return "\n\n---\n\n".join(parts) + "\n"


def main(argv: list[str]) -> int:
    assembled = build()
    if "--check" in argv:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != assembled:
            print("DRIFT: inner-system-prompt.md is out of date. Run: python3 assemble.py")
            return 1
        print(f"OK: inner-system-prompt.md matches its {len(layer_files())} layers.")
        return 0
    OUTPUT.write_text(assembled, encoding="utf-8")
    print(f"Wrote {OUTPUT.name} from {len(layer_files())} layers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
