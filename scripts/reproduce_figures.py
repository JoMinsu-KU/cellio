"""Regenerate publication plots from verified raw evidence and independent finite fixtures."""
import argparse
import csv
import json
from pathlib import Path

from cellio.reporting import report


def reproduce(campaign,output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    evidence=report(campaign,output)
    data=json.loads(Path(evidence["metrics"]).read_text(encoding="utf-8"))
    figures=output/"figures"
    plt.rcParams.update({"font.size":10,"svg.fonttype":"none","axes.spines.top":False,"axes.spines.right":False})
    created=[]
    def save(fig,name):
        fig.tight_layout()
        for extension in ("pdf","png"):
            dest=figures/f"{name}.{extension}"
            fig.savefig(dest,dpi=190,bbox_inches="tight")
            created.append(str(dest))
        plt.close(fig)
    failure=data.get("failure_timeline")
    if failure:
        with (output/failure["csv"]).open(encoding="utf-8") as stream:
            rows=list(csv.DictReader(stream))
        fig,axes=plt.subplots(4,1,figsize=(9,6),sharex=True)
        steps=[int(r["step"]) for r in rows]
        def number(s):
            if s in ("True","False"): return int(s=="True")
            return float(s) if s else float("nan")
        for key,label in (("raw","Raw"),("observed","Observed")):
            axes[0].step(steps,[number(r[key]) for r in rows],where="post",label=label,alpha=.8)
        axes[0].set_ylabel(failure["signal"]); axes[0].legend()
        axes[1].plot(steps,[number(r["actual_q_m"]) for r in rows]);axes[1].set_ylabel("Joint (m)")
        for axis in "xyz":
            axes[2].plot(steps,[number(r["part_"+axis+"_m"]) for r in rows],label=axis)
        axes[2].set_ylabel("Part (m)");axes[2].legend(ncols=3)
        events=tuple((key,label) for key,label in (("issued","Issue"),("applied","Apply"),("fail_witnesses","FAIL")))
        for i,(key,label) in enumerate(events):
            selected=[int(r["step"]) for r in rows if r[key]]
            axes[3].scatter(selected,[i]*len(selected),marker="|")
        axes[3].set_yticks(range(3),[s[1] for s in events]);axes[3].set_xlabel("Physics step")
        for ax in axes:
            window=None
            for r in rows:
                transitions=r["fault_events"].split()
                if "ACTIVE" in transitions: window=int(r["step"])
                if window is not None and "ENDED" in transitions:
                    ax.axvspan(window,int(r["step"]),color="#d9a961",alpha=.2)
                    window=None
            if window is not None: ax.axvspan(window,steps[-1],color="#d9a961",alpha=.2)
        save(fig,"figure3_failure_timeline")
    if any(g["counts"].get("N_start",0) for g in data["groups"]):
        groups=[g for g in data["groups"] if g["group"]["controller"]=="reference"]
        if groups:
            labels=[f'{g["group"]["cell"]} / {g["group"]["arm"]}' for g in groups]
            fig,ax=plt.subplots(figsize=(9,max(3,.36*len(labels))))
            left=[0]*len(labels)
            colors=("#16745f","#69a8b5","#c85856","#d5ac64","#826493","#b5b9bd")
            for label,color in zip(("SUCCESS","CONTROLLED_ABORT","FAILED","UNKNOWN","ERROR","NOT_STARTED"),colors):
                vals=[g["task_distribution"][label]["numerator"] for g in groups]
                ax.barh(labels,vals,left=left,color=color,label=label)
                left=[a+b for a,b in zip(left,vals)]
            ax.set_xlabel("Planned first attempts");ax.legend(ncols=3,fontsize="small")
            save(fig,"figure4_planned_task_outcomes")
    matrix=data["oracle"].get("matrix_4x4",{})
    if matrix:
        labels=("PASS","FAIL","UNKNOWN","NOT_APPLICABLE")
        fig,ax=plt.subplots(figsize=(6,5))
        values=[[matrix[e][a] for a in labels] for e in labels]
        ax.imshow(values,cmap="Blues")
        ax.set_xticks(range(4),labels,rotation=25,ha="right");ax.set_yticks(range(4),labels)
        ax.set(xlabel="Observed verdict",ylabel="Frozen expected verdict")
        for i,row in enumerate(values):
            for j,n in enumerate(row):
                ax.text(j,i,str(n),ha="center",va="center",color="white" if n>24 else "#173349")
        save(fig,"figure5_oracle_fixtures")
    overhead={c:[p["overhead_percent"] for p in obj["pairs"] if p["overhead_percent"] is not None]
              for c,obj in data["overhead"].items()}
    measured={k:v for k,v in overhead.items() if v}
    if measured:
        fig,ax=plt.subplots(figsize=(6,4))
        for i,(cell,values) in enumerate(measured.items()):
            # Points are paired runs; confidence intervals are separately blocked by initial state.
            ax.scatter([i]*len(values),values,alpha=.6)
        ax.set_xticks(range(len(measured)),list(measured));ax.set_ylabel("Paired (B1 − B0) / B0 (%)")
        ax.axhline(0,color="gray",lw=.8);save(fig,"figure6_paired_overhead")
    return {"metrics":evidence["metrics"],"generated":created}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    opts=parser.parse_args()
    print(json.dumps(reproduce(opts.campaign,opts.output),indent=2))
