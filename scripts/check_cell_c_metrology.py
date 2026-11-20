"""Exercise tolerance endpoints and invalid part presentation using only the original CAD."""
import argparse
from hashlib import sha256
import json
from pathlib import Path

from pxr import Gf, Usd, UsdGeom
from cellio.inspection.study import cell_c_inspection_logic as gauge


ROOT=Path(__file__).resolve().parents[1]
CONFIG=ROOT/"protocols/cell-c-metrology-v1.json"
MODEL=Path(gauge.__file__).resolve().with_name("inspection_equipment.usdc")


def measurement_stage():
    stage=Usd.Stage.CreateInMemory()
    UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage,1.)
    equipment=UsdGeom.Xform.Define(stage,"/Equipment").GetPrim()
    equipment.GetReferences().AddReference(MODEL.as_posix())
    part, = [p for p in stage.Traverse() if p.GetName()=="WB01_InspectionBracket"]
    return stage,part


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    protocol=json.loads(CONFIG.read_bytes())
    stage,part=measurement_stage()
    path=part.GetPath()
    endpoint_results=[]
    for error,expected in zip(protocol["drawing_errors_mm"],protocol["expected_classes"],strict=True):
        gauge.set_boss_error(stage,path,error)
        reference=gauge.part_truth(stage,path)
        reading=gauge.measure_part(stage,path)
        measured="OK" if reading.within_tolerance else "NG"
        geometry="OK" if reference["within_tolerance"] else "NG"
        limit=protocol["max_probe_geometry_disagreement_mm"]
        passed=(expected==measured==geometry and abs(reading.height_mm-reference["height_mm"])<=limit
                and abs(reference["height_mm"]-protocol["nominal_mm"]-error)<=limit)
        endpoint_results.append({"drawing_error_mm":error,"expected":expected,"geometry_mm":reference["height_mm"],
                                 "probe_mm":reading.height_mm,"observed":measured,"passed":passed})
    invalid=[]
    for case in protocol["invalid_presentations"]:
        stage,part=measurement_stage()
        form=UsdGeom.Xformable(part)
        form.AddTranslateOp(opSuffix="challenge").Set(Gf.Vec3d(*case["translation_m"]))
        form.AddRotateXOp(opSuffix="challenge").Set(case["rotation_x_deg"])
        try:
            gauge.measure_part(stage,part.GetPath())
            rejection=""
        except gauge.InspectionError as exc:
            rejection=str(exc)
        invalid.append({"case":case["name"],"reason":rejection,
                        "passed":case["error_contains"] in rejection.lower()})
    passed=all(r["passed"] for r in endpoint_results+invalid)
    report={"protocol_sha256":sha256(CONFIG.read_bytes()).hexdigest(),"model_sha256":sha256(MODEL.read_bytes()).hexdigest(),
            "measurement_code_sha256":sha256(Path(gauge.__file__).read_bytes()).hexdigest(),"passed":passed,
            "tolerance_endpoints":endpoint_results,"invalid_presentations":invalid}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes((json.dumps(report,ensure_ascii=False,indent=2)+"\n").encode("utf-8"))
    print(f"{sum(r['passed'] for r in endpoint_results)}/{len(endpoint_results)} dimensions; "
          f"{sum(r['passed'] for r in invalid)}/{len(invalid)} placement errors detected.")
    return 0 if passed else 1


if __name__=="__main__":
    raise SystemExit(main())
