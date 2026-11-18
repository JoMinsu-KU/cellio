"""Record independent verdicts and witnesses for a closed Windows or Linux attempt."""
import argparse
import platform
from pathlib import Path

from cellio.analysis.replay import read_attempt
from cellio.io import digest, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    evidence = read_attempt(args.artifact)
    witnesses = {
        item["requirement_id"]: [item["verdict"], item["first_witness_step"]]
        for item in evidence["requirements"]
    }
    summary = {
        "os": platform.system(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "attempt_id": evidence["attempt_id"],
        "source_digest": evidence["source_digest"],
        "rules_digest": evidence["rules_digest"],
        "witnesses": witnesses,
        "witness_digest": digest(witnesses),
        "online_offline_equal": evidence["online_offline_equal"],
    }
    write_json(args.output, summary)
    print(summary["witness_digest"])
    return 0 if evidence["online_offline_equal"] is True else 5


if __name__ == "__main__":
    raise SystemExit(main())
