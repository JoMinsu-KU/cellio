"""Compare three inspection observers against preassigned, matched Cell C sequences."""
import argparse
from collections import Counter, defaultdict
import hashlib
import itertools
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CASE_PROTOCOL = ROOT / "protocols/cell-c-softwarex-v1.json"
OBSERVERS = ROOT / "protocols/cell-c-observer-comparison-v1.json"
PRIMARY = ROOT / "results/publication/candidate-8ad96bd/primary/first-attempt-index.json"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def output_bin(witness):
    """Compute the world-space bounds of the rotated rectangular bracket base."""
    x, y, z = witness["position_m"]
    w, a, b, c = witness["orientation_wxyz"]
    norm = math.hypot(w, a, b, c)
    require(abs(norm-1) < 2e-5, "invalid recorded orientation")
    w, a, b, c = (v/norm for v in (w, a, b, c))
    # Half-extent of an oriented rectangle projected onto each fixed world axis.
    hx = .062*abs(1-2*(b*b+c*c)) + .041*abs(2*(a*b-w*c))
    hy = .062*abs(2*(a*b+w*c)) + .041*abs(1-2*(a*a+c*c))
    hz = .062*abs(2*(a*c-w*b)) + .041*abs(2*(b*c+w*a))
    if (not 0 <= witness["linear_speed_m_s"] < .005 or
            not 0 <= witness["angular_speed_rad_s"] < .10 or abs(z-.854)+hz >= .0015):
        return "outside"
    for label, center_y in (("OK",-.19),("NG",.16)):
        if abs(x-.50)+hx <= .068 and abs(y-center_y)+hy <= .048:
            return label
    return "outside"


def adjudicate(rows, protocol, case, raw_root):
    seq, placement, condition = case
    design = protocol["sequences"][seq]
    rows = sorted(rows, key=lambda r: r["part_id"])
    require([r["part_id"] for r in rows] == ["P1","P2"], f"{case} ambiguous parts")
    require(len({r["attempt_id"] for r in rows}) == 1, f"{case} mixed attempts")
    pair_id = "-".join(case)
    parts = []
    for n, r in enumerate(rows):
        require(r["case_id"] == pair_id, f"{case} unexpected case identifier")
        target_height = protocol["nominal_mm"] + design["errors_mm"][n]
        geometry = r["geometry_reference"]["height_mm"]
        probe = r["probe"]["height_mm"]
        require(abs(geometry-target_height) <= protocol["reference_geometry_error_limit_mm"], f"{case} drawing")
        require(abs(probe-geometry) <= protocol["probe_to_drawing_limit_mm"], f"{case} measurement")
        actual = output_bin(r["physical_output"])
        require(actual in ("OK","NG") and actual == r["physical_output"]["region"], f"{case} ambiguous output")
        owner = r["decision"]["result_part_id"]
        part = r["part_id"]
        require(owner in ("P1","P2") and part == r["decision"]["part_id"], f"{case} identifier")
        parts.append(dict(part=part,expected=design["truth"][n],actual=actual,owner=owner,
                          geometry_mm=geometry,probe_mm=probe))
    if raw_root:
        from cellio.inspection.replay import extract_witness, replay_witness
        replay = replay_witness(extract_witness(raw_root / pair_id / "attempt-01" / "episode"))
        require(replay["condition"] == condition, f"{case} raw condition")
        for p, q in zip(parts,replay["parts"],strict=True):
            require([p["part"],p["expected"],p["actual"],p["owner"],p["probe_mm"],p["geometry_mm"]] ==
                    [q["part"],q["expected_bin"],q["observed_bin"],q["result_part_id"],
                     q["probe_height_mm"],q["geometry_height_mm"]], f"{case} raw/compact evidence")
    active = condition == "stale_ok" and design["truth"][0] == "OK"
    wrong_bin = any(p["expected"] != p["actual"] for p in parts)
    wrong_owner = any(p["owner"] != p["part"] for p in parts)
    return dict(case=pair_id,sequence=seq,placement=placement,condition=condition,attempt=rows[0]["attempt_id"],
                nominally_triggered=active,flags={"cycle_completion":False,
                "output_quality":wrong_bin,"result_owner":wrong_owner},parts=parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations",type=Path,default=ROOT/"results/qualification/cell-c-softwarex-v1/observations.jsonl")
    parser.add_argument("--raw-root",type=Path,help="also recover and cross-check all observations from original native traces")
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--receipt",type=Path,help="write a separate host and interpreter record for a raw replay")
    args = parser.parse_args()
    design = json.loads(CASE_PROTOCOL.read_bytes())
    observers = json.loads(OBSERVERS.read_bytes())
    cases = list(itertools.product(design["sequences"],design["placement_profiles"],design["conditions"]))
    by_case = defaultdict(list)
    for r in map(json.loads,args.observations.read_bytes().splitlines()):
        key = (r["sequence"],r["placement"],r["condition"])
        require(key in cases, f"unregistered {key}")
        by_case[key].append(r)
    evaluated, missing = [], []
    for case in cases:
        if len(by_case[case]) < 2:
            missing.append({"case":"-".join(case),"complete_observations":len(by_case[case])})
        else:
            evaluated.append(adjudicate(by_case[case], design, case, args.raw_root))
    matrices = {name:dict.fromkeys(("TP","FN","FP","TN"),0) for name in observers["observers"]}
    for run in evaluated:
        for obs, flag in run["flags"].items():
            key = ("TP" if flag else "FN") if run["nominally_triggered"] else ("FP" if flag else "TN")
            matrices[obs][key] += 1
    by_design = {(c["sequence"],c["placement"],c["condition"]):c for c in evaluated}
    pairs = []
    for seq, placement in itertools.product(design["sequences"],design["placement_profiles"]):
        arms = [by_design.get((seq,placement,mode)) for mode in ("normal","stale_ok")]
        if all(arms):
            ref, mut = (arm["parts"][1] for arm in arms)
            pairs.append(dict(sequence=seq,placement=placement,output_changed=ref["actual"]!=mut["actual"],
                              owner_changed=ref["owner"]!=mut["owner"],
                              measured_height_difference_mm=mut["probe_mm"]-ref["probe_mm"]))
    canonical = json.loads(PRIMARY.read_bytes())
    require(len({r["attempt_id"] for r in canonical}) == 820, "unexpected canonical selection")
    tab = Counter((r["task"],r["compliance"]) for r in canonical)
    result = {"schema":"cellio.softwarex.observer-audit/1","inputs_sha256":{
        p.name:sha(p) for p in (CASE_PROTOCOL,OBSERVERS,PRIMARY,args.observations)},"planned":len(cases),
        "scorable":len(evaluated),"unavailable":missing,"matrices":matrices,"paired_P2":pairs,"cases":evaluated,
        "canonical_retrospective":[dict(task=t,compliance=v,attempts=n) for (t,v),n in sorted(tab.items())]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    encoded = (json.dumps(result,indent=2,sort_keys=True,allow_nan=False)+"\n").encode("utf-8")
    args.output.write_bytes(encoded)
    if args.receipt:
        import platform
        from importlib import metadata
        require(args.raw_root is not None,"environment receipt requires native trace readback")
        receipt={"system":platform.system(),"kernel":platform.release(),"python":platform.python_version(),
                 "cellio":metadata.version("cellio"),"case_count":len(evaluated),"part_count":2*len(evaluated),
                 "audit_script_sha256":sha(Path(__file__)),"observer_results_sha256":sha(args.output),
                 "observations_sha256":sha(args.observations),"raw_trace_checked":True}
        args.receipt.parent.mkdir(parents=True,exist_ok=True)
        args.receipt.write_bytes((json.dumps(receipt,indent=2,sort_keys=True)+"\n").encode("utf-8"))
    print(f"{len(evaluated)}/{len(cases)} cases | {len(pairs)} pairs | SHA256 {sha(args.output)}")


if __name__ == "__main__":
    main()
