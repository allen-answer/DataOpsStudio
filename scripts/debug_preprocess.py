"""Debug preprocess.normalize_for_parsing."""
from __future__ import annotations

import re
import sys

from app.lineage.preprocess import (
    _normalize_insert_alias_prefix,
    normalize_for_parsing,
)


def main(path: str) -> int:
    text = open(path, encoding="utf-8").read()
    print(f"=== INPUT ({len(text)} chars) ===")
    print(text[:500])
    print()

    # 直接测 regex
    pattern = re.compile(
        r"(?ix)"
        r"\b(insert\s+into\s+[\w$#.\"`\[\]]+)"
        r"(\s+)([A-Za-z_][\w$#]*)"
        r"(\s*\(\s*)",
    )
    matches = list(pattern.finditer(text))
    print(f"=== Regex matches: {len(matches)} ===")
    for m in matches:
        print(f"  pos={m.start()}-{m.end()}, alias='{m.group(3)}', groups={m.groups()!r}")

    print()
    print("=== Just _normalize_insert_alias_prefix ===")
    out2 = _normalize_insert_alias_prefix(text)
    diff = len(out2) != len(text) or out2 != text
    print(f"changed={diff}")
    print(out2[:500])

    print()
    print("=== AFTER full normalize_for_parsing ===")
    out = normalize_for_parsing(text)
    print(f"changed_full={out != text}")
    print(out[:500])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/test.sql"))
