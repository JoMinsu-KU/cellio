"""Render Cell C's second-part decisions from the released matched-observer audit."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle


ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    (False,False):("Current result, correct tray","#d7e8ed"),
    (True,False):("Previous result, correct tray","#f5d088"),
    (True,True):("Previous result, wrong tray","#cf745f"),
    (False,True):("Current result, wrong tray","#c9b9dc"),
    None:("Unavailable","#d8d8d8"),
}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("audit",type=Path)
    p.add_argument("destination",type=Path)
    args=p.parse_args()
    report=json.loads(args.audit.read_bytes())
    design=json.loads((ROOT/"protocols/cell-c-softwarex-v1.json").read_bytes())
    lookup={(r["sequence"],r["placement"],r["condition"]):r for r in report["cases"]}
    names=list(design["sequences"])
    columns=[("A","normal"),("B","normal"),("A","stale_ok"),("B","stale_ok")]
    with plt.rc_context({"font.family":"DejaVu Sans","pdf.fonttype":42,"font.size":10}):
        fig, ax=plt.subplots(figsize=(8.15,4.9))
        fig.subplots_adjust(left=.33,right=.98,top=.83,bottom=.20)
        used=set()
        for y, name in enumerate(names):
            for x, (placement,mode) in enumerate(columns):
                run=lookup.get((name,placement,mode))
                if run:
                    part=run["parts"][1]
                    code=(part["owner"] != part["part"],part["actual"] != part["expected"])
                    label=part["actual"]
                else:
                    code,label=None,"—"
                used.add(code)
                ax.add_patch(Rectangle((x-.5,y-.5),1,1,facecolor=LABELS[code][1],edgecolor="white",linewidth=2))
                ax.text(x,y,label,ha="center",va="center",color="#18292e",fontweight="bold")
        axis_labels=[]
        for seq in design["sequences"].values():
            h=[design["nominal_mm"]+d for d in seq["errors_mm"]]
            axis_labels.append(f"{h[0]:.2f}  →  {h[1]:.2f}     {seq['truth'][1]}")
        ax.set(xlim=(-.5,3.5),ylim=(len(names)-.5,-.5),yticks=range(len(names)),yticklabels=axis_labels,
               xticks=range(4),xticklabels=["A","B","A","B"])
        ax.tick_params(axis="both",length=0,pad=6)
        ax.xaxis.tick_top()
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.axvline(1.5,color="white",linewidth=6)
        fig.text(.006,.937,"Part heights (mm)  /  expected P2 tray",ha="left",fontsize=9,color="#344b55")
        ax.text(.25,1.16,"Current-result controller",transform=ax.transAxes,ha="center",fontweight="bold")
        ax.text(.75,1.16,"Stale-OK controller",transform=ax.transAxes,ha="center",fontweight="bold")
        fig.text(.01,.126,"A and B denote the two registered placement profiles. Each box names the observed tray for P2.",fontsize=9)
        handles=[Patch(facecolor=color,label=label) for key,(label,color) in LABELS.items() if key in used]
        fig.legend(handles=handles,loc="lower center",bbox_to_anchor=(.5,.018),frameon=False,ncol=min(3,len(handles)),fontsize=9)
        args.destination.mkdir(parents=True,exist_ok=True)
        for suffix in ("png","pdf"):
            metadata = {"Creator":"CellIO","CreationDate":None,"ModDate":None} if suffix == "pdf" else None
            fig.savefig(args.destination/f"cell-c-inspection.{suffix}",dpi=200,facecolor="white",metadata=metadata)
        plt.close(fig)


if __name__=="__main__":
    main()
