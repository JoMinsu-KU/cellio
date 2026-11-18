"""Native executable for populating an official, local Isaac asset cache."""

import argparse

from .application import worker_main
from .assets import collect_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--source")
    args = parser.parse_args()
    collect_assets(args.root, source=args.source)
    return 0


if __name__ == "__main__":
    raise SystemExit(worker_main(main))
