"""Publication panels derived from the frozen implementations and replayed trials."""

import re
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path

from publication_sources import ROOT, ports, read

from cellio.io import file_hash

PALETTE = {
    "SUCCESS": "#18775d",
    "PASS": "#18775d",
    "CONTROLLED_ABORT": "#6294ac",
    "FAILED": "#b94b47",
    "FAIL": "#b94b47",
    "UNKNOWN": "#d9a957",
    "ERROR": "#8c688d",
    "NOT_STARTED": "#d2d7da",
    "NOT_APPLICABLE": "#6b94b5",
    "UNAVAILABLE": "#cbd1d5",
    "KILLED": "#18775d",
    "MUTATION_UNINFORMATIVE": "#8daac0",
    "NOT_EXERCISED": "#d2b47d",
    "INFRA_ERROR": "#aa5953",
    "INELIGIBLE": "#cbd1d5",
    "SKIPPED_PRECONDITION": "#7894ae",
    "INCOMPLETE": "#cbd1d5",
}


def pending(ax, text):
    ax.set_axis_off()
    ax.text(
        0.5,
        0.5,
        text,
        ha="center",
        va="center",
        transform=ax.transAxes,
        color="#576775",
        wrap=True,
    )


def architecture(plt, save):
    from matplotlib.patches import FancyBboxPatch

    evidence = ports()
    fig, ax = plt.subplots(figsize=(12, 5.6))
    ax.axis("off")
    ax.set(xlim=(0, 12), ylim=(0, 5.6))

    def box(x, y, w, h, title, subtitle):
        ax.add_patch(
            FancyBboxPatch(
                (x, y),
                w,
                h,
                boxstyle="round,pad=0.04,rounding_size=0.07",
                facecolor="#eff4f6",
                edgecolor="#76959c",
            )
        )
        ax.text(
            x + w / 2,
            y + h * 0.65,
            title,
            ha="center",
            va="center",
            weight="bold",
            fontsize=10,
        )
        ax.text(
            x + w / 2, y + h * 0.26, subtitle, ha="center", va="center", fontsize=8.4
        )

    def arrow(start, end):
        ax.annotate(
            "",
            xy=end,
            xytext=start,
            arrowprops={"arrowstyle": "-|>", "color": "#46636a", "lw": 1.4},
        )

    ax.text(0.05, 5.28, "Control path", fontsize=13, weight="bold")
    nodes = [
        (0.10, 2.1, "PlantPort", "snapshot · 120 Hz"),
        (2.65, 2.15, "SignalMapperPort", "nominal sample"),
        (5.25, 2.45, "FaultEngine", "observation transform"),
        (8.18, 3.55, "ControllerPort", "tick(ControllerView, Tick) · 30 Hz"),
    ]
    for x, w, title, desc in nodes:
        box(x, 3.90, w, 0.90, title, desc)
    for left, right in pairwise(nodes):
        arrow((left[0] + left[1] + 0.05, 4.35), (right[0] - 0.07, 4.35))
    ax.text(8.20, 3.63, "ControllerView fields", fontsize=9, weight="bold", va="top")
    fields = evidence["controller_view_fields"]
    ax.text(
        8.20,
        3.36,
        " · ".join(fields[:2]) + "\n" + " · ".join(fields[2:]),
        fontsize=8,
        va="top",
    )
    ax.text(0.05, 2.84, "Evidence and actuation", fontsize=13, weight="bold")
    box(
        0.10,
        1.28,
        2.65,
        1.0,
        "TickEvidenceFrame",
        "truth · raw/observed signals\ncommands · application readback",
    )
    box(3.32, 1.28, 2.02, 1.0, "OraclePort", "observe(frame)")
    box(5.95, 1.28, 2.0, 1.0, "TracePort", "append_tick\n(frame, verdicts)")
    box(
        8.56,
        1.28,
        3.18,
        1.0,
        "CommandRouter → PlantPort",
        "deliveries + actuator overrides\napply · advance_one_step",
    )
    arrow((2.81, 1.8), (3.26, 1.8))
    arrow((5.39, 1.8), (5.90, 1.8))
    arrow((11.35, 3.85), (11.35, 2.37))
    ax.annotate(
        "recorded physical measurements and command receipts",
        xy=(1.42, 1.23),
        xytext=(6.0, 0.36),
        ha="center",
        fontsize=8.4,
        arrowprops={
            "arrowstyle": "-|>",
            "color": "#718890",
            "connectionstyle": "angle,angleA=180,angleB=-90,rad=4",
        },
    )
    ax.plot([10.15, 10.15, 8.02], [1.25, 0.4, 0.4], color="#718890", lw=1)
    save(fig, "figure1_architecture")


def workcells(plt, save, root=None):
    directory = Path(root) if root else ROOT / "results/workstation/figures"
    index = directory / "fixed-camera.json"
    if not index.is_file():
        return False
    captures = read(index)["panels"]
    if [p["cell"] for p in captures] != ["cell_a", "cell_b"]:
        raise ValueError("Two fixed-camera panels in A/B order are required")
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), layout="constrained")
    for ax, p in zip(axes, captures, strict=True):
        name = p["png"]
        if Path(name).name != name or any(
            not re.fullmatch("[0-9a-f]{64}", p[k])
            for k in (
                "png_sha256",
                "manifest_final_sha256",
                "trace_chain_sha256",
                "trace_record_sha256",
            )
        ):
            raise ValueError("Invalid still or source identity")
        if (
            file_hash(directory / name) != p["png_sha256"]
            or p["source"] != "isaac_gui_viewport"
            or p["step"] % 4
        ):
            raise ValueError(
                "Fixed-camera render source differs from the reviewed native capture"
            )
        pixels = plt.imread(directory / name)
        ax.imshow(pixels)
        ax.axis("off")
        ax.set_title(
            f"{p['cell'].replace('_', ' ').title()}  ·  {p['stage']}  ·  step {p['step']}",
            fontsize=12,
        )
    save(fig, "figure2_native_workcells")
    return True


def stack(ax, names, rows, keys):
    left = [0] * len(names)
    for key in keys:
        values = [r.get(key, 0) for r in rows]
        if not any(values):
            continue
        ax.barh(
            range(len(names)),
            values,
            left=left,
            label=key.replace("_", " ").title(),
            color=PALETTE.get(key, "#89949a"),
        )
        left = [a + b for a, b in zip(left, values, strict=True)]
    ax.set_yticks(range(len(names)), names)
    ax.invert_yaxis()
    ax.legend(
        loc="upper left", bbox_to_anchor=(0, -0.07), ncols=2, fontsize=7, frameon=False
    )
    ax.grid(axis="x", alpha=0.15)


def coverage(plt, save, data):
    groups = data.get("groups", [])
    if not any(g["counts"].get("N_start") for g in groups):
        return False
    by = defaultdict(lambda: defaultdict(Counter))
    for g in groups:
        row = by[g["group"]["cell"], g["group"]["arm"]]
        row["counts"].update(g["counts"])
        for axis in ("task_distribution", "compliance_distribution"):
            row[axis].update({k: v["numerator"] for k, v in g[axis].items()})
    rows = [by[k] for k in sorted(by)]
    names = [" / ".join(k) for k in sorted(by)]
    fig, axes = plt.subplots(
        1,
        3,
        figsize=(15.8, max(5, 0.38 * len(rows) + 2.0)),
        width_ratios=[1.2, 1.15, 1.15],
    )
    lo = axes[0]
    lo.set_title("Fault realization", loc="left")
    for i, r in enumerate(rows):
        c = r["counts"]
        n = c["N_faultplan"]
        a = c["N_activate"]
        realized = c["N_realized"]
        if n:
            lo.barh(i, n, color="#dce2e5")
            lo.barh(i, a, color="#8bb3c1")
            lo.barh(i, realized, color="#226e76")
            lo.text(n + 0.2, i, f"{realized}/{n}", va="center", fontsize=8)
        else:
            lo.text(0.2, i, "Healthy arm", va="center", fontsize=8, color="#62727b")
    lo.set_yticks(range(len(rows)), names)
    lo.invert_yaxis()
    lo.set_xlim(0, max(r["counts"]["N_faultplan"] for r in rows) * 1.26 + 2)
    lo.set_xlabel("Realized / planned fault attempts")
    from matplotlib.patches import Patch

    lo.legend(
        handles=[
            Patch(color=c, label=l)
            for c, l in [
                ("#226e76", "Realized"),
                ("#8bb3c1", "Activated"),
                ("#dce2e5", "Planned"),
            ]
        ],
        bbox_to_anchor=(0, -0.07),
        loc="upper left",
        ncols=2,
        fontsize=7,
        frameon=False,
    )
    for ax, axis, title, keys in [
        (
            axes[1],
            "task_distribution",
            "Task outcome",
            (
                "SUCCESS",
                "CONTROLLED_ABORT",
                "FAILED",
                "UNKNOWN",
                "ERROR",
                "NOT_STARTED",
            ),
        ),
        (
            axes[2],
            "compliance_distribution",
            "Requirement compliance",
            ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE", "UNAVAILABLE"),
        ),
    ]:
        stack(ax, names, [r[axis] for r in rows], keys)
        ax.tick_params(labelleft=False)
        ax.set_title(title, loc="left")
        ax.set_xlabel("Planned first attempts")
    save(fig, "figure4_realization_task_compliance")
    return True


def oracle_mutants(plt, save, oracle, canonical):
    matrix = oracle.get("matrix_4x4")
    if not matrix:
        return False
    fig, (left, right) = plt.subplots(1, 2, figsize=(11.5, 5))
    labels = ("PASS", "FAIL", "UNKNOWN", "NOT_APPLICABLE")
    vals = [[matrix[e][a] for a in labels] for e in labels]
    left.imshow(vals, cmap="Blues")
    left.set(
        xticks=range(4),
        yticks=range(4),
        xticklabels=("Pass", "Fail", "Unknown", "N/A"),
        yticklabels=("Pass", "Fail", "Unknown", "N/A"),
        xlabel="Independent oracle",
        ylabel="Frozen gold verdict",
        title=f"Gold fixtures · {sum(map(sum, vals))} cases",
    )
    for i, row in enumerate(vals):
        for j, v in enumerate(row):
            left.text(
                j,
                i,
                str(v),
                ha="center",
                va="center",
                color="white" if v > max(map(max, vals)) * 0.55 else "#173349",
            )
    models = canonical.get("mutants", {}).get("models", {})
    if any(p["candidate"] and p["reference"] for rows in models.values() for p in rows):
        names = sorted(models)
        counts = [Counter(p["status"] for p in models[n]) for n in names]
        stack(
            right,
            [n.rsplit(".", 1)[-1] for n in names],
            counts,
            (
                "KILLED",
                "MUTATION_UNINFORMATIVE",
                "NOT_EXERCISED",
                "INFRA_ERROR",
                "INELIGIBLE",
            ),
        )
        right.set(
            xlabel="Registered mutant/reference pairs",
            title="Paired targeted violations",
        )
    else:
        pending(right, "Mutant/reference pairs\nAwaiting canonical paired attempts")
    save(fig, "figure5_oracle_mutant_validation")
    return True


def stability(plt, save, suites):
    overhead = suites.get("overhead-v1", {}).get("overhead", {})
    resets = suites.get("reset-v1", {}).get("reset_chains", {}).get("chains", [])
    soak = suites.get("soak-v1", {}).get("soak", {})
    paired = {
        c: [
            p["overhead_percent"]
            for p in d["pairs"]
            if p["overhead_percent"] is not None
        ]
        for c, d in overhead.items()
    }
    have_reset = any(r["source_artifact_ids"] for r in resets)
    verified_soak = bool(soak.get("worker_pid") and soak.get("measurement_end_ns"))
    if not (any(paired.values()) or have_reset or verified_soak):
        return False
    fig, axes = plt.subplots(1, 3, figsize=(13, 4.1))
    if any(paired.values()):
        for i, (cell, values) in enumerate(paired.items()):
            axes[0].scatter([i] * len(values), values, alpha=0.5)
        axes[0].set_xticks(range(len(paired)), paired)
        axes[0].axhline(0, color="#8e969a", lw=0.7)
        axes[0].set(ylabel="(B1 − B0) / B0 (%)", title="Paired execution overhead")
    else:
        pending(axes[0], "Awaiting verified B0/B1 pairs")
    if have_reset:
        counts = Counter(r["status"] for r in resets)
        names = list(counts)
        axes[1].barh(
            names, [counts[k] for k in names], color=[PALETTE[k] for k in names]
        )
        axes[1].set(xlabel="Planned three-episode chains", title="Reset recovery")
    else:
        pending(axes[1], "Awaiting recorded reset chains")
    if verified_soak:
        found = False
        for key, color in (("rss", "#186c77"), ("vram", "#ae674d")):
            points = soak[key]
            if points["first20_median"] is None:
                continue
            axes[2].plot(
                [0, 1],
                [points[n] / 2**30 for n in ("first20_median", "last20_median")],
                marker="o",
                label=key.upper(),
                color=color,
            )
            found = True
        if found:
            axes[2].legend()
            axes[2].set_xticks([0, 1], ["First 20", "Last 20"])
            axes[2].set(
                ylabel="Median end-of-episode GiB",
                title=f"Soak · {soak['measured_completed']} runs · {soak['elapsed_wall_seconds'] / 3600:.1f} h",
            )
        else:
            pending(
                axes[2], "Verified soak window\nInsufficient process memory samples"
            )
    else:
        pending(axes[2], "Awaiting verified timed soak")
    save(fig, "figure6_overhead_reset_soak")
    return True
