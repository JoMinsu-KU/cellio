"""Render the registered upper-cross matched pair at its recorded final placements."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from cellio.adapters.isaac62.application import standalone_main, start_application
from cellio.inspection.replay import extract_witness, replay_witness

CASES = ("upper_cross-A-normal", "upper_cross-A-stale_ok")
EYE = (1.02, -0.81, 1.58)
TARGET = (0.475, -0.023, 0.878)
UP = (0.0, 0.0, 1.0)
W, H = 1260, 900
CAMERA = "/World/PublicationTrayCamera"
FOCAL = 49.0
APERTURE = 36.0


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def endpoint(root, case):
    attempt = root / case / "attempt-01"
    folder = attempt / "episode"
    allocation = read(root / "allocation.json")
    row = next(r for r in allocation["rows"] if r["id"] == case)
    recipe = read(attempt.parent / "recipe.json")
    result = read(folder / "result.json")
    actual = replay_witness(extract_witness(folder))
    (event,) = [
        e
        for e in read(folder / "events.json")
        if e["event"] == "output_settled" and e["part"] == "P2"
    ]
    piece = actual["parts"][1]
    observed = result["parts"][1]["physical_output"]
    if (
        read(attempt / "status.json")["status"] != "completed"
        or any(recipe[k] != v for k, v in row.items())
        or result["recipe"] != recipe
        or event["frame"] != result["frames"]
        or piece["observed_bin"] != event["observed"]
        or not observed["seated"]
        or observed["part"] != "P2"
        or event["part"] != piece["part"]
        or abs(piece["probe_height_mm"] - 33.25) > 0.020
    ):
        raise ValueError(f"{case}: source or endpoint mismatch")
    sources = {
        name: digest(folder / name)
        for name in (
            "result.json",
            "events.json",
            "motion.jsonl",
            "replay.usda",
            "live.usda",
            "stage/stage-manifest.json",
            "stage/cell_c_inspection.usda",
            "stage/inspection_equipment.usdc",
            "stage/floor_epoxy.png",
        )
    }
    sources["recipe.json"] = digest(attempt.parent / "recipe.json")
    sources["status.json"] = digest(attempt / "status.json")
    return folder, {
        "case": case,
        "attempt": "attempt-01",
        "current_part": piece["part"],
        "frame": event["frame"],
        "part_local_height_mm": piece["geometry_height_mm"],
        "probe_height_mm": piece["probe_height_mm"],
        "result_owner": piece["result_part_id"],
        "observed_bin": piece["observed_bin"],
        "settled_position_m": observed["position_m"],
        "settled_quaternion_wxyz": observed["orientation_wxyz"],
        "input_sha256": sources,
    }


def capture(app, folder, evidence, path):
    import numpy as np
    import omni.replicator.core as rep
    import omni.timeline
    import omni.usd
    from PIL import Image
    from pxr import Gf, Usd, UsdGeom

    context = omni.usd.get_context()
    if not context.open_stage(str(folder / "replay.usda")):
        raise RuntimeError("Cannot open measured replay")
    for _ in range(20):
        app.update()
    stage = context.get_stage()
    if (
        not stage
        or stage.GetRootLayer().realPath.replace("\\", "/").lower()
        != (folder / "replay.usda").as_posix().lower()
    ):
        raise RuntimeError("Unexpected active replay")
    t = evidence["frame"]
    hz = stage.GetTimeCodesPerSecond()
    lo, hi = stage.GetStartTimeCode(), stage.GetEndTimeCode()
    if hz != 30 or not lo <= t <= hi or stage.GetPrimAtPath(CAMERA):
        raise RuntimeError(
            f"Invalid stage timing or camera collision: {lo}..{hi}, {hz} Hz"
        )

    with Usd.EditContext(stage, stage.GetSessionLayer()):
        camera = UsdGeom.Camera.Define(stage, CAMERA)
        camera.CreateProjectionAttr(UsdGeom.Tokens.perspective)
        camera.CreateFocalLengthAttr(FOCAL)
        camera.CreateHorizontalApertureAttr(APERTURE)
        camera.CreateVerticalApertureAttr(APERTURE * H / W)
        camera.CreateClippingRangeAttr(Gf.Vec2f(0.02, 5.0))
        camera.AddTransformOp().Set(
            Gf.Matrix4d(1)
            .SetLookAt(Gf.Vec3d(*EYE), Gf.Vec3d(*TARGET), Gf.Vec3d(*UP))
            .GetInverse()
        )
    at = Usd.TimeCode(t)
    observed = stage.GetPrimAtPath("/World/ActiveParts/P2")
    cache = UsdGeom.XformCache(at)
    local = cache.GetLocalToWorldTransform(observed)
    position = list(local.ExtractTranslation())
    q = local.ExtractRotationQuat().GetNormalized()
    quat = [q.GetReal(), *q.GetImaginary()]
    ori = evidence["settled_quaternion_wxyz"]
    if (
        math.dist(position, evidence["settled_position_m"]) > 1e-6
        or min(math.dist(quat, ori), math.dist(quat, [-v for v in ori])) > 2e-5
        or UsdGeom.Imageable(observed).ComputeVisibility(at) != UsdGeom.Tokens.inherited
        or UsdGeom.Imageable(
            stage.GetPrimAtPath("/World/ActiveParts/P1")
        ).ComputeVisibility(at)
        != UsdGeom.Tokens.invisible
    ):
        raise ValueError(
            "Reopened stage does not match the final measured P2 pose / P1 takeaway"
        )
    timeline = omni.timeline.get_timeline_interface()
    timeline.pause()
    timeline.set_looping(False)
    timeline.set_current_time(t / hz)
    app.update()
    rep.orchestrator.set_capture_on_play(False)
    product = rep.create.render_product(CAMERA, (W, H))
    rgb = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb.attach([product])
    try:
        for _ in range(5):
            rep.orchestrator.step(rt_subframes=4, delta_time=0.0, pause_timeline=True)
        tick = timeline.get_current_time() * hz
        if abs(tick - t) > 1e-5:
            raise RuntimeError(f"Rendered timecode {tick} != endpoint {t}")
        pixels = np.asarray(rgb.get_data())
        if pixels.shape[:2] != (H, W) or pixels.ndim != 3 or pixels[:, :, :3].std() < 4:
            raise RuntimeError("Empty or incomplete native camera frame")
        Image.fromarray(pixels[:, :, :3]).save(path)
        evidence.update(
            rendered_timecode=tick,
            fps=hz,
            stage_range=[lo, hi],
            staged_position_m=position,
            staged_quaternion_wxyz=quat,
            raw_image_sha256=digest(path),
        )
    finally:
        rgb.detach([product])
        product.destroy()
        for _ in range(5):
            app.update()
        context.close_stage()
    for key in ("replay.usda", "live.usda", "stage/cell_c_inspection.usda"):
        if digest(folder / key) != evidence["input_sha256"][key]:
            raise RuntimeError(f"The source stage changed: {key}")


def compose(panels, case_images, path):
    from matplotlib import font_manager
    from PIL import Image, ImageDraw, ImageFont

    def font(size, weight):
        return ImageFont.truetype(
            font_manager.findfont(
                font_manager.FontProperties(family="DejaVu Sans", weight=weight)
            ),
            size,
        )

    pad, gap, header = 43, 32, 78
    page = Image.new("RGB", (2 * pad + 2 * W + gap, header + H + 168), "white")
    draw = ImageDraw.Draw(page)
    ink = (20, 29, 32)
    for i, (row, image_path) in enumerate(zip(panels, case_images, strict=True)):
        x = pad + i * (W + gap)
        heading = ("(a)   Normal controller", "(b)   Stale-OK controller")[i]
        draw.text((x, 14), heading, font=font(45, "bold"), fill=ink)
        with Image.open(image_path) as content:
            page.paste(content, (x, header))
        text = f"Current part  {row['current_part']}     ·     Result owner  {row['result_owner']}     ·     Observed tray  {row['observed_bin']}"
        draw.text((x, header + H + 18), text, font=font(34, "normal"), fill=ink)
    probe = panels[0]["probe_height_mm"]
    if round(probe, 3) != round(panels[1]["probe_height_mm"], 3):
        raise ValueError(
            "Panel captions require matching probe measurements at the published precision"
        )
    common = f"P2 measured {probe:.3f} mm in both runs. The drawing specifies 33.000 ± 0.200 mm."
    draw.text((pad, header + H + 94), common, font=font(34, "normal"), fill=ink)
    page.save(path, dpi=(350, 350), optimize=True)


def main(args):
    root = args.root.resolve()
    pairs = [endpoint(root, case) for case in CASES]
    scratch = args.frames.resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    app = start_application(
        {
            "headless": True,
            "renderer": "PathTracing",
            "samples_per_pixel_per_frame": 32,
            "denoiser": True,
            "multi_gpu": False,
            "limit_cpu_threads": 4,
            "disable_viewport_updates": True,
            "width": 600,
            "height": 400,
        }
    )
    images = []
    for folder, row in pairs:
        img = scratch / (row["case"] + ".png")
        capture(app, folder, row, img)
        images.append(img)
    rows = [r for _, r in pairs]
    compose(rows, images, args.output)
    receipt = {
        "schema": "cellio.inspection.publication-camera/1",
        "series": root.name,
        "allocation_sha256": digest(root / "allocation.json"),
        "script_sha256": digest(Path(__file__)),
        "camera": {
            "eye_m": EYE,
            "target_m": TARGET,
            "up": UP,
            "focal_mm": FOCAL,
            "horizontal_aperture_mm": APERTURE,
            "projection": "perspective",
            "raw_size_px": [W, H],
            "renderer": "Isaac Sim 6.2 RTX PathTracing",
            "samples_per_pixel_per_frame": 32,
            "rt_subframes": 4,
            "render_steps_per_still": 5,
            "timeline_delta_s": 0.0,
        },
        "composition": "Matching camera projection; full native RGB panels, with source-derived text in the outer margins.",
        "panels": rows,
        "figure": args.output.name,
        "figure_sha256": digest(args.output),
    }
    args.receipt.write_text(
        json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(f"{args.output}: {receipt['figure_sha256']}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--frames",
        required=True,
        type=Path,
        help="New directory for the two unannotated captures",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    arguments = parser.parse_args()
    raise SystemExit(standalone_main(lambda: main(arguments)))
