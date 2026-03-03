from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path
import sys

from .pipeline import run_pipeline
from .utils import load_dotenv


def _parse_date(value: str | None) -> date:
    if not value:
        return datetime.now().date()
    return datetime.strptime(value, "%Y-%m-%d").date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Daily infographic generator")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one daily generation")
    run.add_argument("--domain", default="ai", help="domain name, e.g. ai")
    run.add_argument("--config", default=None, help="path to domain config YAML")
    run.add_argument("--date", default=None, help="run date in YYYY-MM-DD")
    run.add_argument("--mock-only", action="store_true", help="use only mock sources")
    run.add_argument("--output-dir", default=None, help="override output directory")

    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        run_date = _parse_date(args.date)
        config_path = args.config or str(Path("configs") / "domains" / f"{args.domain}.yaml")

        result = run_pipeline(
            config_path=config_path,
            run_date=run_date,
            mock_only=args.mock_only,
            output_dir=args.output_dir,
        )

        print(f"image: {result.image_path}")
        print(f"manifest: {result.manifest_path}")
        print(
            f"fetched={result.fetched_count}, events={result.event_count}, selected={result.selected_count}"
        )
        if result.skipped_sources:
            print("skipped:", ", ".join(result.skipped_sources))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
