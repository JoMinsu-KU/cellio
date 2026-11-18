"""Disposable Kit process for the workstation compatibility smoke test."""
import sys
import traceback
from pathlib import Path
from cellio.io import write_json


def probe(level):
    from cellio.adapters.isaac62.application import release_application, start_application
    app=start_application({"headless":True,"disable_viewport_updates":True,"multi_gpu":False})
    try:
        import omni.kit.app
        import omni.timeline
        manager=omni.kit.app.get_app().get_extension_manager()
        for name in ("isaacsim.robot_motion.experimental.motion_generation", "isaacsim.robot_motion.cumotion"):
            manager.set_extension_enabled_immediate(name, True)
            if not manager.is_extension_enabled(name):
                raise RuntimeError(f"Cannot enable {name}")
        import isaacsim.robot_motion.cumotion as motion
        import warp as wp
        from isaacsim.core.simulation_manager import SimulationManager, PhysxScene
        from isaacsim.core.experimental.prims import RigidPrim
        from pxr import Gf, UsdGeom, UsdPhysics
        import omni.usd
        names=("isaacsim.robot_motion.cumotion","omni.physx","isaacsim.core.simulation_manager")
        versions={name:manager.get_enabled_extension_id(name) for name in names}
        robot=motion.load_cumotion_supported_robot("franka")
        info={"extensions":versions,"warp_runtime_version": wp.__version__,
              "tool_frames":list(robot.robot_description.tool_frame_names()),
              "kit_version":omni.kit.app.get_app().get_build_version(),"smoke_steps":0}
        if level=="smoke":
            SimulationManager.switch_physics_engine("physx")
            stage=omni.usd.get_context().get_stage()
            UsdGeom.SetStageMetersPerUnit(stage,1.0)
            UsdGeom.SetStageUpAxis(stage,UsdGeom.Tokens.z)
            scene=UsdPhysics.Scene.Define(stage,"/World/PhysicsScene")
            scene.CreateGravityDirectionAttr().Set(Gf.Vec3f(0,0,-1))
            scene.CreateGravityMagnitudeAttr().Set(9.81)
            scene_api=PhysxScene("/World/PhysicsScene")
            scene_api.set_steps_per_second(120)
            scene_api.set_solver_type("TGS")
            scene_api.set_enabled_gpu_dynamics(False)
            prim=UsdGeom.Cube.Define(stage,"/World/SmokeCube")
            prim.CreateSizeAttr(.04)
            prim.AddTranslateOp().Set(Gf.Vec3d(0,0,10))
            UsdPhysics.RigidBodyAPI.Apply(prim.GetPrim())
            UsdPhysics.CollisionAPI.Apply(prim.GetPrim())
            jaw=UsdGeom.Cube.Define(stage,"/World/SmokeSlider")
            jaw.CreateSizeAttr(.02)
            jaw.AddTranslateOp().Set(Gf.Vec3d(.5,0,1))
            UsdPhysics.RigidBodyAPI.Apply(jaw.GetPrim())
            UsdPhysics.MassAPI.Apply(jaw.GetPrim()).CreateMassAttr(.25)
            UsdPhysics.CollisionAPI.Apply(jaw.GetPrim())
            joint=UsdPhysics.PrismaticJoint.Define(stage,"/World/SmokeSlideJoint")
            joint.CreateBody1Rel().SetTargets([jaw.GetPath()])
            joint.CreateLocalPos0Attr(Gf.Vec3f(.5,0,1))
            joint.CreateAxisAttr("X")
            joint.CreateLowerLimitAttr(-.05)
            joint.CreateUpperLimitAttr(0.)
            drive=UsdPhysics.DriveAPI.Apply(joint.GetPrim(),"linear")
            drive.CreateTypeAttr("force")
            drive.CreateStiffnessAttr(4000.)
            drive.CreateDampingAttr(100.)
            drive.CreateMaxForceAttr(30.)
            drive.CreateTargetPositionAttr(0.)
            drive.CreateTargetVelocityAttr(0.)
            SimulationManager.setup_simulation(dt=1/120,device="cpu")
            SimulationManager.enable_fabric(True)
            omni.timeline.get_timeline_interface().play()
            SimulationManager.initialize_physics()
            body=RigidPrim("/World/SmokeCube")
            def height():
                positions,_=body.get_world_poses()
                return float(positions.numpy()[0,2])
            z0=height()
            clock0=SimulationManager.get_simulation_time()
            for _ in range(120):
                SimulationManager.step(steps=1,update_fabric=True)
            z1=height()
            clock1=SimulationManager.get_simulation_time()
            if not body.is_physics_tensor_entity_valid():
                raise RuntimeError("Physics tensor view is invalid")
            if abs((clock1-clock0)-1.)>1e-5 or not 4.7 < z0-z1 < 5.2:
                raise RuntimeError(f"Unexpected 1 s free fall: {z0} -> {z1}")
            slider=RigidPrim("/World/SmokeSlider")
            x0=float(slider.get_world_poses()[0].numpy()[0,0])
            accepted=drive.GetTargetPositionAttr().Set(-.015)
            target=float(drive.GetTargetPositionAttr().Get())
            for _ in range(60):
                SimulationManager.step(steps=1,update_fabric=True)
            x1=float(slider.get_world_poses()[0].numpy()[0,0])
            if not accepted or abs(target+.015)>1e-6 or abs((x1-x0)-target)>.001:
                raise RuntimeError(f"Prismatic target readback {target}, physical displacement {x1-x0}")
            info.update(smoke_steps=180,freefall_steps=120,start_z_m=z0,end_z_m=z1,clock_delta_s=clock1-clock0,
                        drive_steps=60,drive_target_m=target,drive_displacement_m=x1-x0,
                        solver=scene_api.get_solver_type(),physics_device=str(SimulationManager.get_device()))
        return {"passed":True,**info}
    finally:
        if level == "smoke":
            omni.timeline.get_timeline_interface().stop()
        release_application(app)


def main():
    try:
        report=probe(sys.argv[2])
    except Exception as exc:
        report={"passed":False,"type":type(exc).__name__,"error":str(exc)}
        traceback.print_exc()
    write_json(Path(sys.argv[1]),report)
    return 0 if report["passed"] else 3


if __name__=="__main__":
    from cellio.adapters.isaac62.application import standalone_main
    sys.exit(standalone_main(main))
