"""Live smoke test:  python -m discovery --language python --interest "web scraping" """
import argparse
import json

from . import discover
from .mock import sample_response


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m discovery", description=__doc__)
    p.add_argument("--level", default="beginner", choices=["beginner", "intermediate", "advanced"])
    p.add_argument("--language", action="append", default=[], help="repeatable")
    p.add_argument("--interest", action="append", default=[], help="repeatable")
    p.add_argument("--keyword", action="append", default=[], help="repeatable")
    p.add_argument("--candidate", action="append", default=[],
                   help="library name to verify, e.g. requests or npm:express (repeatable)")
    p.add_argument("--mock", action="store_true", help="print sample data, no network")
    args = p.parse_args(argv)
    if args.mock:
        result = sample_response()
    else:
        result = discover({"experience_level": args.level, "languages": args.language,
                           "interests": args.interest, "keywords": args.keyword,
                           "candidate_libraries": args.candidate})
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
