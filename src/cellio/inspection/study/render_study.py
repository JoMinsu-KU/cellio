"""Assemble the equipment with the installed Franka asset and photograph the static USD in Isaac Sim."""
import argparse
from pathlib import Path

from cellio.adapters.isaac62.application import standalone_main, start_application

HERE = Path(__file__).resolve().parent
VIEWS = {
    "Overview": ((2.14, -3.25, 2.18), (-.035, .012, .69), 39.0),
    "Deck": ((.76, -1.66, 1.95), (.0, .0, .94), 41.0),
    "Gauge": ((.22, -.54, 1.18), (.100, .013, .954), 56.0),
}


def create_stage(path, robot_file):
    import numpy as np
    from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade

    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1)
    world = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(world.GetPrim())
    root = stage.GetRootLayer()
    root.documentation = "Cell C | dimensional inspection and sorting. Static visual design study in metres."
    eye, target, _ = VIEWS["Overview"]
    root.customLayerData = {"cameraSettings": {"boundCamera": "/World/Overview",
                            "Perspective": {"position": Gf.Vec3d(*eye), "target": Gf.Vec3d(*target)}}}

    def material(name, color, roughness):
        mat = UsdShade.Material.Define(stage, "/World/Surface/" + name)
        shader = UsdShade.Shader.Define(stage, mat.GetPath().AppendChild("Shader"))
        shader.CreateIdAttr("UsdPreviewSurface")
        shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(*color))
        shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(roughness)
        mat.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
        return mat

    def slab(name, center, size, mat):
        obj = UsdGeom.Cube.Define(stage, "/World/Room/" + name)
        obj.CreateSizeAttr(1)
        obj.AddTranslateOp().Set(Gf.Vec3d(*center))
        obj.AddScaleOp().Set(Gf.Vec3f(*size))
        UsdShade.MaterialBindingAPI.Apply(obj.GetPrim()).Bind(mat)

    floor = material("Epoxy", (.33, .36, .36), .49)
    slab("FloorUnderlay", (0, 0, -.03), (100, 100, .03), floor)
    surface = material("CoatedConcrete", (.33, .36, .36), .49)
    paint = UsdShade.Shader(stage.GetPrimAtPath("/World/Surface/CoatedConcrete/Shader"))
    uv = UsdShade.Shader.Define(stage, "/World/Surface/CoatedConcrete/Coordinates")
    uv.CreateIdAttr("UsdPrimvarReader_float2")
    uv.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    sample = UsdShade.Shader.Define(stage, "/World/Surface/CoatedConcrete/Finish")
    sample.CreateIdAttr("UsdUVTexture")
    sample.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(
        Sdf.AssetPath("./floor_epoxy.png"))
    sample.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
    for axis in ["wrapS", "wrapT"]:
        sample.CreateInput(axis, Sdf.ValueTypeNames.Token).Set("repeat")
    sample.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv.ConnectableAPI(), "result")
    paint.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(sample.ConnectableAPI(), "rgb")
    for ix in range(-2, 3):
        for iy in range(-2, 3):
            cx, cy, half = ix*3.0+.26, iy*3.0+.51, 1.499
            tile = UsdGeom.Mesh.Define(stage, f"/World/Room/Floor_{ix+2}_{iy+2}")
            tile.CreateSubdivisionSchemeAttr("none")
            points = [(cx-half,cy-half,0),(cx+half,cy-half,0),(cx+half,cy+half,0),(cx-half,cy+half,0)]
            tile.CreatePointsAttr(points)
            tile.CreateFaceVertexCountsAttr([4])
            tile.CreateFaceVertexIndicesAttr([0,1,2,3])
            st = UsdGeom.PrimvarsAPI(tile).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray, "vertex")
            st.Set([(x*.80, y*.80) for x,y,_ in points])
            UsdShade.MaterialBindingAPI.Apply(tile.GetPrim()).Bind(surface)
    equipment = UsdGeom.Xform.Define(stage, "/World/Equipment").GetPrim()
    equipment.GetReferences().AddReference("./inspection_equipment.usdc")
    if not list(equipment.GetChildren()):
        raise RuntimeError("The local equipment layer failed to compose.")
    mount = UsdGeom.Xform.Define(stage, "/World/Robot")
    mount.AddTranslateOp().Set(Gf.Vec3d(-.21, .17, .832))
    robot = UsdGeom.Xform.Define(stage, "/World/Robot/Panda").GetPrim()
    robot.GetReferences().AddReference(robot_file.as_posix())
    robot.GetVariantSets().SetSelection("Mesh", "quality")
    robot.GetVariantSets().SetSelection("Physics", "none")
    stage.Load()
    accessory_root = "/World/Robot/Panda/panda_link0/geometry/"
    for name in ("cables_connected", "extrudedSurface1", "extrudedSurface2", "pCylinder1", "pCylinder2"):
        accessory = stage.GetPrimAtPath(accessory_root + name)
        if accessory.IsValid():
            UsdGeom.Imageable(accessory).MakeInvisible()

    # The vendor stores its rigid links as siblings at their zero-joint poses.
    # For this static camera study, apply each joint rotation about its local Z.
    images = {}
    for p in robot.GetChildren():
        if p.GetName().startswith("panda_") and p.IsA(UsdGeom.Xformable):
            images[p.GetName()] = np.asarray(UsdGeom.Xformable(p).GetLocalTransformation()).T.copy()
    names = [f"panda_link{n}" for n in range(8)]
    previous = np.eye(4)
    for name, angle in zip(names[1:], (.14, -.55, -.08, -2.03, .06, 1.55, .72)):
        c, s = np.cos(angle), np.sin(angle)
        rotation = np.array([[c,-s,0,0],[s,c,0,0],[0,0,1,0],[0,0,0,1]])
        neutral = images[name]
        posed = previous @ neutral @ rotation
        previous = posed @ np.linalg.inv(neutral)
        UsdGeom.Xformable(robot.GetChild(name)).MakeMatrixXform().Set(Gf.Matrix4d(posed.T.tolist()))
    for name in ("panda_hand", "panda_leftfinger", "panda_rightfinger"):
        if name in images:
            posed = previous @ images[name]
            UsdGeom.Xformable(robot.GetChild(name)).MakeMatrixXform().Set(Gf.Matrix4d(posed.T.tolist()))

    def aim(obj, eye, target):
        matrix = Gf.Matrix4d(1).SetLookAt(Gf.Vec3d(*eye), Gf.Vec3d(*target), Gf.Vec3d(0,0,1))
        obj.AddTransformOp().Set(matrix.GetInverse())

    for view, (eye, target, focal) in VIEWS.items():
        camera = UsdGeom.Camera.Define(stage, "/World/" + view)
        aim(camera, eye, target)
        camera.CreateFocalLengthAttr(focal)
        camera.CreateHorizontalApertureAttr(28.8 if view == "Overview" else 36.0)
        camera.CreateVerticalApertureAttr(22.5)
        camera.CreateClippingRangeAttr(Gf.Vec2f(.01, 50))
    dome = UsdLux.DomeLight.Define(stage, "/World/Rig/Ambient")
    dome.CreateIntensityAttr(420.0)
    dome.CreateColorAttr(Gf.Vec3f(.91, .95, 1.0))
    for name, pos, focus, width, height, power, color in (
        ("Window", (-1.4, -.9, 2.9), (0,0,.8), 2.0, 1.2, 5500, (1,.91,.80)),
        ("CeilingStrip", (1.0, .85, 2.55), (0,.1,.75), 1.7, .32, 6000, (.82,.91,1)),
        ("FrontFill", (1.1,-1.8,1.55), (0,0,.75), .85,.72,2300,(1,.98,.95)),
    ):
        fixture = UsdLux.RectLight.Define(stage, "/World/Rig/" + name)
        fixture.CreateWidthAttr(width)
        fixture.CreateHeightAttr(height)
        fixture.CreateIntensityAttr(power)
        fixture.CreateColorAttr(Gf.Vec3f(*color))
        aim(fixture, pos, focus)
    root.Save()
    return len(list(stage.Traverse()))


def make_floor_texture(path):
    """Local, seamless epoxy grain; no photographic source material."""
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(714)
    size = 768
    y, x = np.mgrid[:size, :size] * (2.0 * np.pi / size)
    tone = np.zeros((size, size))
    for scale, amp in [(2,1.7),(6,1.4),(12,.9),(30,.45)]:
        for _ in range(4):
            k, l = rng.integers(1, scale+1, size=2)
            tone += amp * np.sin(k*x + l*y + rng.random()*2*np.pi)
    tone += rng.normal(0, 3.0, tone.shape)
    fleck = rng.random(tone.shape) < .022
    tone[fleck] -= rng.uniform(5, 17, fleck.sum())
    pixels = np.clip(np.array([155,163,161]) + tone[:,:,None], 0, 255).astype(np.uint8)
    Image.fromarray(pixels).save(path)


def run(out, assets):
    from cellio.inspection.study.prepare import prepare_stage
    app = start_application({"headless": True, "renderer": "PathTracing", "multi_gpu": False,
                             "limit_cpu_threads": 4, "width": 640, "height": 400,
                             "samples_per_pixel_per_frame": 48,
                             "denoiser": True, "disable_viewport_updates": True})
    import numpy as np
    import omni.usd
    import omni.replicator.core as rep
    from PIL import Image
    stage_name = prepare_stage(out, assets)
    context = omni.usd.get_context()
    if not context.open_stage(str(stage_name)):
        raise RuntimeError(f"Could not open {stage_name}")
    for _ in range(35):
        app.update()
    rep.orchestrator.set_capture_on_play(False)
    for view in VIEWS:
        dims = (1536, 1200) if view == "Overview" else (1680, 1050)
        product = rep.create.render_product("/World/" + view, dims)
        rgb = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb.attach([product])
        for _ in range(5):
            rep.orchestrator.step(rt_subframes=2, delta_time=0.0, pause_timeline=True)
        frame = np.asarray(rgb.get_data())
        if frame.ndim != 3 or frame.shape[:2] != (dims[1], dims[0]) or frame.std() < 3:
            raise RuntimeError(f"Missing or empty native frame {view}: {frame.shape}")
        path = out / (view.lower() + ".png")
        Image.fromarray(frame[:,:,:3]).save(path)
        print(f"CAPTURE {view} {frame.shape} {frame[:,:,:3].mean():.1f} | {path}", flush=True)
        rgb.detach([product])
        product.destroy()
        for _ in range(5):
            app.update()
    print(f"Rendered {stage_name}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--assets", type=Path)
    args = parser.parse_args()
    raise SystemExit(standalone_main(lambda: run(args.out.resolve(), args.assets)))
