from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import achievements


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Suckling achievement catalog JSON.")
    parser.add_argument(
        "output",
        nargs="?",
        default="../../Sites/sucklingsite/assets/data/achievements.json",
        help="Path to write the JSON catalog.",
    )
    args = parser.parse_args()

    output = Path(args.output)
    if not output.is_absolute():
        output = Path(__file__).resolve().parent.parent / output
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "version": 1,
        "total": len(achievements.catalog_entries()),
        "categories": ["rentals", "rb9 library", "reviews", "macguffins", "games", "discovery", "letterboxd"],
        "achievements": achievements.catalog_entries(),
    }
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {payload['total']} achievements to {output}")


if __name__ == "__main__":
    main()
