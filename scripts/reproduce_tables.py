"""Regenerate denominator, outcome, statistical, and evidence tables from raw campaigns."""
import argparse
import csv
import json
from pathlib import Path

from cellio.reporting import report


def write_table(path, fieldnames, rows):
    with path.open("w",encoding="utf-8",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def reproduce(campaign,output):
    produced=report(campaign,output)
    payload=json.loads(Path(produced["metrics"]).read_text(encoding="utf-8"))
    tables=output/"tables"
    rates=[]
    outcomes=[]
    for group in payload["groups"]:
        key=group["group"]
        for name,m in group["rates"].items():
            ci=m.get("bootstrap",{})
            rates.append({**key,"metric":name,"numerator":m["numerator"],"denominator":m["denominator"],
                          "fraction":m["value"],"lower95":ci.get("lower_95"),"upper95":ci.get("upper_95"),
                          "undefined_bootstrap_fraction":ci.get("undefined_resample_fraction"),
                          "exclusions":json.dumps(m["exclusions_with_reason"],sort_keys=True)})
        for kind in ("task_distribution","compliance_distribution"):
            for label,metric in group[kind].items():
                outcomes.append({**key,"axis":kind,"outcome":label,"count":metric["numerator"],
                                 "planned":metric["denominator"],"fraction":metric["value"]})
    write_table(tables/"coverage_rates.csv",
        ["cell","arm","controller","metric","numerator","denominator","fraction","lower95","upper95",
         "undefined_bootstrap_fraction","exclusions"],rates)
    write_table(tables/"task_compliance.csv",
        ["cell","arm","controller","axis","outcome","count","planned","fraction"],outcomes)
    performance=[{"cell_mode":key,"measure":metric,"unit":value["unit"],**{
        k:value[k] for k in ("denominator","median","p95","p99","max")}}
        for key,data in payload["performance"].items() for metric,value in data.items()]
    write_table(tables/"performance.csv",["cell_mode","measure","unit","denominator","median","p95","p99","max"],performance)
    index=payload["oracle"].get("matrix_4x4",{})
    write_table(tables/"oracle_confusion.csv",["expected","actual","count"],
                ({"expected":e,"actual":a,"count":n} for e,labels in index.items() for a,n in labels.items()))
    return produced


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    options=parser.parse_args()
    print(json.dumps(reproduce(options.campaign,options.output),indent=2))
