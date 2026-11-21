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
    parser.add_argument(
        "--observed-only",
        action="store_true",
        help="Check agreement of verdicts and first witnesses when original rules are unverified",
    )
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
        "observed_results_equal": evidence["observed_results_equal"],
        "same_rule_revision": evidence["same_rule_revision"],
        "rule_identity_status": evidence["rule_identity_status"],
    }
    write_json(args.output, summary)
    print(summary["witness_digest"])
    check = "observed_results_equal" if args.observed_only else "online_offline_equal"
    return 0 if evidence[check] is True else 5


if __name__ == "__main__":
    raise SystemExit(main())
