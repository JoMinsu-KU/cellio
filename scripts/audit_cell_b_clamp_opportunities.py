"""Retrospective command-opportunity audit of all canonical Cell B F4 first attempts."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "results/publication/candidate-8ad96bd/primary/first-attempt-index.json"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_episode(directory, row):
    manifest = directory / "manifest.final.json"
    if digest(manifest) != row["manifest_sha256"]:
        raise ValueError(f"Unsealed manifest for {row['attempt_id']}")
    files = json.loads(manifest.read_bytes())["files"]
    for name in ("commands.jsonl.gz","events.jsonl.gz","faults.json"):
        if digest(directory/name) != files[name]["sha256"]:
            raise ValueError(f"Input differs from sealed {name}")
    fault, = json.loads((directory/"faults.json").read_bytes())
    start, end = fault["actual_start"],fault["actual_end"]
    if fault["family"] != "F4" or fault["target_id"] != "actuator.clamp" or end-start != 120:
        raise ValueError("Unexpected registered clamp fault")
    with gzip.open(directory/"commands.jsonl.gz","rt",encoding="utf-8") as stream:
        intents = [rec["data"] for rec in map(json.loads,stream) if rec["kind"] == "intents"
                   and rec["data"]["actuator_id"] == "actuator.clamp"]
    with gzip.open(directory/"events.jsonl.gz","rt",encoding="utf-8") as stream:
        enters = [evt["available_step"] for rec in map(json.loads,stream) if
                  (evt:=rec["data"])["event_type"]=="controller.stage_enter" and
                  evt["payload"].get("stage_id")=="clamp_close"]
    if enters != [start]:
        raise ValueError(f"Unexpected trigger availability {enters} at {start}")
    # The canonical B controller encodes clamp-close as a negative joint position.
    close_steps = [i["issued_step"] for i in intents if i["kind"] == "JOINT_POSITION" and i["value"] < 0]
    return {"attempt_id":row["attempt_id"],"controller":row["controller"],"scene_seed":row["scene_seed"],
            "repeat":row["repeat"],"trigger_step":start,"end_exclusive":end,"issued_close_steps":close_steps,
            "all_target_intents_in_window":sum(start<=i["issued_step"]<end for i in intents),
            "delayed_packets":fault["delayed_packet_count"],"manifest_sha256":row["manifest_sha256"]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("canonical",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    idx=[r for r in json.loads(INDEX.read_bytes()) if r["cell"]=="cell_b" and r["arm"]=="f4"]
    if Counter(r["controller"] for r in idx) != {"reference":40,"mutant.c_m4":10}:
        raise ValueError("Canonical F4 denominator changed")
    rows=[read_episode(args.canonical/r["run_id"]/r["attempt_id"],r) for r in idx]
    subsets={}
    for controller in sorted({r["controller"] for r in rows}):
        cohort=[r for r in rows if r["controller"]==controller]
        offsets=[n-r["trigger_step"] for r in cohort for n in r["issued_close_steps"]]
        subsets[controller]={"attempts":len(cohort),"any_close_intent_anywhere":sum(bool(r["issued_close_steps"]) for r in cohort),
            "any_target_intent_during_fault":sum(r["all_target_intents_in_window"]>0 for r in cohort),
            "any_delayed_packets":sum(r["delayed_packets"]>0 for r in cohort),
            "close_issue_minus_trigger_steps":dict(sorted(Counter(offsets).items()))}
    report={"type":"retrospective_full_episode_opportunity_audit","source_index_sha256":digest(INDEX),
            "groups":subsets,"attempts":rows}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes((json.dumps(report,indent=2,sort_keys=True)+"\n").encode("utf-8"))
    print(json.dumps(subsets))


if __name__=="__main__":
    main()
