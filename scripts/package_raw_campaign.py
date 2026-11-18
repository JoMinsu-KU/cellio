"""Archive the registered canonical ledger and attempts for offline replay."""

import argparse
import os
from pathlib import Path
import shutil
import tempfile
import zipfile

from cellio.analysis.campaign import attempt_histories, lines, safe_child
from cellio.io import file_hash
from cellio.release import check_archived_index, raw_index
from cellio.runtime.campaign import exclusive, read_plan


def package(campaign, target):
    campaign, target = campaign.resolve(), target.resolve()
    if target.is_relative_to(campaign) or target.exists():
        raise ValueError("Choose a new archive path outside the campaign")
    target.parent.mkdir(parents=True, exist_ok=True)
    with exclusive(campaign), tempfile.TemporaryDirectory(dir=target.parent) as scratch:
        index = raw_index(campaign)
        plan = read_plan(campaign / "campaign.plan.json")
        histories = attempt_histories(
            lines(campaign / "ledger.jsonl"), {r["run_id"] for r in plan["runs"]}
        )
        files = [campaign / "campaign.plan.json", campaign / "ledger.jsonl"]
        for run_id, attempts in histories.items():
            for events in attempts:
                if events[-1]["state"] not in {
                    "COMPLETED",
                    "CANCELLED",
                    "INFRA_ERROR",
                    "SKIPPED_PRECONDITION",
                }:
                    raise ValueError("An attempt still has an open ledger entry")
                attempt = safe_child(campaign, run_id, events[0]["attempt_id"])
                # Include retained partial files on any failed retry.
                for path in sorted(attempt.rglob("*")):
                    if path.is_symlink() or not path.resolve().is_relative_to(attempt):
                        raise ValueError(
                            f"Unexpected link inside the raw attempt: {path}"
                        )
                    if path.is_file():
                        files.append(path)
        output = Path(scratch) / "campaign.zip"
        with zipfile.ZipFile(output, "x", allowZip64=True) as archive:
            for file in files:
                if file.is_symlink():
                    raise ValueError(f"Unexpected link in campaign inputs: {file}")
                entry = zipfile.ZipInfo(file.relative_to(campaign).as_posix())
                entry.compress_type = (
                    zipfile.ZIP_STORED
                    if file.suffix in {".gz", ".mp4"}
                    else zipfile.ZIP_DEFLATED
                )
                with (
                    file.open("rb") as src,
                    archive.open(entry, "w", force_zip64=True) as dst,
                ):
                    shutil.copyfileobj(src, dst, 1024 * 1024)
        if not check_archived_index(output, index):
            raise ValueError("Archive did not reproduce the completed first attempts")
        # Publish without replacing an archive created concurrently.
        if os.name == "nt":
            output.rename(target)
        else:
            target.hardlink_to(output)
    return file_hash(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    print(package(options.campaign, options.output))
