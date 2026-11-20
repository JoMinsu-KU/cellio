"""Render the measured native USD replay for the normal two-part cycle."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

from cellio.adapters.isaac62.application import standalone_main, start_application


def capture(folder, stills_only=False):
    app = start_application({"headless":True,"renderer":"RaytracedLighting","multi_gpu":False,
                             "limit_cpu_threads":4,"disable_viewport_updates":True,
                             "width":800,"height":500})
    import numpy as np
    import omni.replicator.core as rep
    import omni.timeline
    import omni.usd
    from PIL import Image, ImageDraw, ImageFont

    result = json.loads((folder/"result.json").read_text(encoding="utf-8"))
    events = json.loads((folder/"events.json").read_text(encoding="utf-8"))
    context = omni.usd.get_context()
    if not context.open_stage(str(folder/"replay.usda")):
        raise RuntimeError("Cannot open measured playback stage")
    for _ in range(25):
        app.update()
    timeline = omni.timeline.get_timeline_interface()
    rep.orchestrator.set_capture_on_play(False)
    w,h = 800,500
    product = rep.create.render_product("/World/Deck",(w,h))
    rgb = rep.AnnotatorRegistry.get_annotator("rgb")
    rgb.attach([product])
    steps = sorted(set(range(0,result["frames"]+1,6)) | {e["frame"] for e in events} | {result["frames"]})
    milestones = {e["frame"]: e for e in events if e["event"] in ("inspection","output_settled")}
    if stills_only:
        steps = sorted(milestones)
    encoder = shutil.which("ffmpeg")
    if not stills_only and not encoder:
        raise RuntimeError("ffmpeg is required to export the native camera playback")
    proc = None if stills_only else subprocess.Popen([encoder,"-hide_banner","-loglevel","error","-y","-f","rawvideo",
                             "-pixel_format","rgb24","-video_size",f"{w}x{h}","-framerate","15",
                             "-i","-","-an","-c:v","libx264","-preset","veryfast","-pix_fmt","yuv420p",
                             "-movflags","+faststart",str(folder/"cycle.mp4")],stdin=subprocess.PIPE)
    font = ImageFont.load_default(size=15)
    images = []
    try:
        for t in steps:
            timeline.set_current_time(t/30.)
            app.update()
            rep.orchestrator.step(rt_subframes=2, delta_time=0.0, pause_timeline=True)
            frame = np.asarray(rgb.get_data())
            if frame.shape[:2] != (h,w) or frame.std() < 3:
                raise RuntimeError(f"Empty camera at frame {t}")
            im = Image.fromarray(frame[:,:,:3])
            images.append(float(frame[:,:,:3].mean()))
            active = [e for e in events if e["frame"] <= t]
            current = active[-1] if active else {}
            piece = current.get("part","")
            label = {"infeed_index":"Indexed at infeed","grasp":"Grasp at " + current.get("station",""),
                     "inspection":"Height inspection","release":"Release at " + current.get("station",""),
                     "output_settled":"Seated in " + current.get("observed",""),
                     "external_takeaway":"First part removed from single-place tray"}.get(current.get("event"),"Initial state")
            draw = ImageDraw.Draw(im)
            draw.rectangle((0,h-27,w,h),fill=(19,27,32))
            draw.text((12,h-25),f"Cell C  |  {result['condition']}  |  {piece}  {label}",fill="white",font=font)
            draw.text((w-93,h-25),f"{t/30:5.1f} s",fill="white",font=font)
            if t in milestones:
                e = milestones[t]
                im.save(folder / f"{e['part']}-{e['event']}.png")
            if proc is not None:
                proc.stdin.write(im.tobytes())
    finally:
        code = 0
        if proc is not None:
            proc.stdin.close()
            code = proc.wait()
        rgb.detach([product])
        product.destroy()
    if code or max(images)-min(images)<.15:
        raise RuntimeError(f"Movie encoder or scene variation failed: {code}, range {max(images)-min(images)}")
    print(f"CAPTURED {len(steps)} native frames in {folder}",flush=True)
    return 0


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder",type=Path)
    parser.add_argument("--stills-only",action="store_true")
    args=parser.parse_args()
    raise SystemExit(standalone_main(lambda:capture(args.folder.resolve(),args.stills_only)))
