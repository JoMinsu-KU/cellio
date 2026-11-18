"""Portable, dependency-free HTML, Markdown and vector figures for verified results."""

import csv
import html
from io import StringIO
from pathlib import Path
from xml.sax.saxutils import escape

from cellio.artifacts import frames
from cellio.io import atomic_write


def table(path, rows, columns):
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write(path, stream.getvalue().encode("utf-8"))


def timeline(trial, destination):
    target = trial.fault.get("target_id")
    if target and target.startswith("actuator."):
        target = "gripper.closed" if target == "actuator.gripper" else "clamp.closed"
    target = target or "gripper.closed"
    rows = []
    stage = None
    for f in frames(trial.path):
        k = f["step"]
        controller = f.get("controller_output") or {}
        stage = controller.get("stage_id", stage)
        raw = f["raw_signals"].get(target, {})
        obs = f["observed_signals"].get(target, {})
        truth = f["truth"]
        part = truth["bodies"].get("part_000", {})
        pos = part.get("position_m") or (None, None, None)
        j = truth["joints"].get(
            "clamp" if trial.row["cell"] == "cell_b" else "panda_finger_joint1", {}
        )
        witnesses = [
            r["requirement_id"]
            for r in trial.eval["requirements"]
            if r["verdict"] == "FAIL" and r["first_witness_step"] == k
        ]
        rows.append(
            {
                "step": k,
                "stage": stage,
                "signal": target,
                "raw": raw.get("value"),
                "observed": obs.get("value"),
                "quality": obs.get("quality"),
                "source_step": obs.get("source_step"),
                "actual_q_m": j.get("position"),
                "part_x_m": pos[0],
                "part_y_m": pos[1],
                "part_z_m": pos[2],
                "issued": " ".join(c["command_id"] for c in f["intents"]),
                "applied": " ".join(
                    c["command_id"] for c in f["applied"] if c["status"] == "APPLIED"
                ),
                "fault_events": " ".join(e["kind"] for e in f["fault_transitions"]),
                "fail_witnesses": " ".join(witnesses),
            }
        )
    if not rows:
        return None
    table(destination / "tables" / "failure_timeline.csv", rows, list(rows[0]))
    series = (
        ("raw", "Raw " + target),
        ("observed", "Observed " + target),
        ("actual_q_m", "Measured joint (m)"),
        ("part_z_m", "Part height (m)"),
    )
    span = max(r["step"] for r in rows) or 1
    svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="920" height="510" viewBox="0 0 920 510">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<g font-family="sans-serif" font-size="12" fill="#233547">',
        f'<text x="16" y="22">Attempt {escape(trial.attempt_id)} · {escape(trial.row["cell"])} · {escape(trial.row["arm"])}</text>',
    ]
    start, end = trial.fault.get("actual_start"), trial.fault.get("actual_end")

    def xpos(k):
        return 135 + 755 * k / span

    for i, (key, label) in enumerate(series):
        y = 54 + i * 94
        nums = [float(r[key]) for r in rows if isinstance(r[key], (float, int))]
        svg.append(f'<text x="16" y="{y + 10}">{escape(label)}</text>')
        if not nums:
            svg.append(f'<text x="140" y="{y + 20}">No observation</text>')
            continue
        lo, hi = min(nums), max(nums)
        if hi == lo:
            hi = lo + 1
        if start is not None:
            svg.append(
                f'<rect x="{xpos(start):.2f}" y="{y}" width="{xpos(end if end is not None else span) - xpos(start):.2f}" height="60" fill="#f5e6dc"/>'
            )
        svg.append(
            f'<text x="95" y="{y + 5}">{hi:.3g}</text><text x="95" y="{y + 61}">{lo:.3g}</text>'
        )
        parts = []
        pen = False
        for r in rows:
            value = r[key]
            if value is None:
                pen = False
                continue
            parts.append(
                f"{'L' if pen else 'M'}{xpos(r['step']):.2f},{y + 60 - 60 * (value - lo) / (hi - lo):.2f}"
            )
            pen = True
        svg.append(
            f'<path fill="none" stroke="#17667b" stroke-width="1.2" d="{" ".join(parts)}"/>'
        )
    for i, (key, label, color) in enumerate(
        (
            ("issued", "Issued", "#1a719e"),
            ("applied", "Applied", "#207750"),
            ("fail_witnesses", "FAIL witness", "#a93838"),
        )
    ):
        y = 425 + 17 * i
        svg.append(f'<text x="16" y="{y + 8}">{label}</text>')
        for r in rows:
            if r[key]:
                x = xpos(r["step"])
                svg.append(
                    f'<line x1="{x:.2f}" x2="{x:.2f}" y1="{y}" y2="{y + 10}" stroke="{color}"><title>{escape(r[key])}</title></line>'
                )
    svg.append(
        f'<text x="135" y="495">0</text><text x="470" y="495">Physics step</text><text x="860" y="495">{span}</text></g></svg>'
    )
    atomic_write(
        destination / "figures" / "failure_timeline.svg", "".join(svg).encode("utf-8")
    )
    return {
        "attempt_id": trial.attempt_id,
        "trace": str(trial.path / "trace.jsonl.gz"),
        "signal": target,
        "fault_family": trial.fault.get("family"),
        "trace_chain_sha256": trial.eval["source_digest"],
        "csv": "tables/failure_timeline.csv",
        "figure": "figures/failure_timeline.svg",
    }


def seed_section(data):
    title = "Secondary sensitivity by development exposure"
    headers = (
        "Scene seed",
        "Cell",
        "Cell states",
        "Planned",
        "First attempts",
        "Started",
        "Evaluable",
        "Task success / planned",
        "Task success / evaluable",
    )
    rows = []
    for seeds, cohort in data["cohorts"].items():
        for cell_name, summary in (("All cells", cohort), *cohort["by_cell"].items()):
            counts = summary["counts"]
            per_plan = summary["task_success_per_plan"]
            per_eval = summary["task_success_per_evaluable"]
            rows.append(
                (
                    seeds,
                    cell_name,
                    counts["N_distinct_cell_states"],
                    counts["N_plan"],
                    counts["N_first_attempt"],
                    counts["N_start"],
                    counts["N_eval"],
                    f"{per_plan['numerator']}/{per_plan['denominator']}",
                    f"{per_eval['numerator']}/{per_eval['denominator']}"
                    if per_eval["denominator"]
                    else "Undefined (0 evaluable)",
                )
            )
    target = "tables/seed_sensitivity.json"
    label = "Counts by cell, controller and arm, with run and first-attempt provenance"
    markdown = [
        "",
        f"## {title}",
        "",
        data["interpretation"],
        "",
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *["| " + " | ".join(map(str, row)) + " |" for row in rows],
        "",
        f"[{label}]({target})",
    ]
    html_rows = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in row) + "</tr>"
        for row in rows
    )
    html_section = (
        f"<h2>{title}</h2><p>{html.escape(data['interpretation'])}</p><table><thead><tr>"
        + "".join(f"<th>{col}</th>" for col in headers)
        + f'</tr></thead><tbody>{html_rows}</tbody></table><p><a href="{target}">{label}</a></p>'
    )
    return markdown, html_section


def render_report(metrics, out):
    fields = (
        "cell",
        "arm",
        "controller",
        "N_plan",
        "N_start",
        "N_eval",
        "N_faultplan",
        "N_trigger",
        "N_activate",
        "N_realized",
        "N_clear",
        "N_error",
    )
    entries = [{**group["group"], **group["counts"]} for group in metrics["groups"]]
    table(out / "tables" / "campaign_flow.csv", entries, fields)

    def cell(value):
        return html.escape(str(value))

    head = "".join(f"<th>{cell(f)}</th>" for f in fields)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell(row.get(f, 0))}</td>" for f in fields) + "</tr>"
        for row in entries
    )
    summary = metrics["overall"]["counts"]
    title = f"CellIO · {metrics['suite_id']}"
    text = f"Planned {summary.get('N_plan', 0)} · Started {summary.get('N_start', 0)} · Evaluable {summary.get('N_eval', 0)} · Infrastructure errors {summary.get('N_error', 0)}"
    sections = (
        "oracle",
        "mutants",
        "overhead",
        "performance",
        "reset_chains",
        "soak",
        "scene_reuse",
        "delays",
    )
    blocks = []
    markdown = [
        f"# {title}",
        text,
        "## Campaign flow",
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
        *[
            "| " + " | ".join(str(row.get(f, 0)) for f in fields) + " |"
            for row in entries
        ],
    ]
    sensitivity_html = ""
    if "seed_sensitivity" in metrics:
        sensitivity_md, sensitivity_html = seed_section(metrics["seed_sensitivity"])
        markdown.extend(sensitivity_md)
    for section in sections:
        label = section.replace("_", " ").title()
        blocks.append(f'<li><a href="tables/{section}.json">{cell(label)}</a></li>')
        markdown.extend(("", f"[{label}](tables/{section}.json)"))
    failure = metrics.get("failure_timeline")
    chart = ""
    if failure:
        chart = '<h2>Failure timeline</h2><img width="920" src="figures/failure_timeline.svg" alt="Aligned sensor, command, joint and part evidence"><p><a href="tables/failure_timeline.csv">Full tick table including stage and witnesses</a></p>'
        markdown.extend(
            (
                "",
                "## Failure timeline",
                "",
                "![Failure timeline](figures/failure_timeline.svg)",
                "",
                "[Full tick table](tables/failure_timeline.csv)",
            )
        )
    links = []
    for r in metrics["attempts"]:
        ident = r["attempt_id"]
        if ident and r.get("path"):
            path = Path(r["path"])
            from os.path import relpath

            try:
                url = Path(relpath(path / "trace.jsonl.gz", out)).as_posix()
            except ValueError:
                url = (path / "trace.jsonl.gz").as_uri()
            links.append(
                f'<li><a href="{cell(url)}">{cell(ident)}</a> · {cell(r["outcome"])}</li>'
            )
    md = "\n\n".join(markdown[:3]) + "\n\n" + "\n".join(markdown[3:]) + "\n"
    html_text = f'<!doctype html><html lang="en"><meta charset="utf-8"><title>{cell(title)}</title><style>body{{font:15px system-ui;color:#182c37;max-width:1080px;margin:40px auto}}table{{border-collapse:collapse}}td,th{{text-align:left;padding:5px 8px;border-bottom:1px solid #ddd}}img{{max-width:100%}}</style><h1>{cell(title)}</h1><p>{cell(text)}</p><h2>Campaign flow</h2><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>{sensitivity_html}{chart}<h2>Analyses</h2><ul>{"".join(blocks)}</ul><p><a href="metrics.json">Complete metrics and eligibility reasons</a></p><details><summary>Verified attempt traces</summary><ul>{"".join(links)}</ul></details></html>'
    atomic_write(out / "report.md", md.encode("utf-8"))
    atomic_write(out / "report.html", html_text.encode("utf-8"))
