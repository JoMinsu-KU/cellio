"""Original modular inspection-bay walls and aisle marking. Dimensions in metres."""

from math import cos, pi, sin

import bmesh
import bpy
from mathutils import Vector


def build_context():
    col = bpy.data.collections.new("Inspection_bay_context")
    bpy.context.scene.collection.children.link(col)
    root = bpy.data.objects.new("Inspection_bay_architecture", None)
    col.objects.link(root)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = .1
    shapes = {}

    def finish(name, rgb, metal, rough):
        m = bpy.data.materials.new("Bay_" + name)
        m.diffuse_color = (*rgb, 1)
        m.use_nodes = True
        shader = m.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (*rgb, 1)
        shader.inputs["Metallic"].default_value = metal
        shader.inputs["Roughness"].default_value = rough
        return m

    warm = finish("Mineral_paint", (.58,.60,.565), .04,.75)
    base = finish("Scrub_resistant_wainscot", (.305,.365,.36), .12,.56)
    joint = finish("Recessed_panel_joint", (.18,.22,.215), .11,.68)
    trim = finish("Satin_zinc_frame", (.38,.43,.43), .67,.41)
    polymer = finish("Window_gasket", (.13,.165,.17), .02,.64)
    light = finish("Satin_clerestory", (.62,.72,.72), .15,.38)
    cabinet = finish("Powder_coat_service_gray", (.37,.42,.415), .25,.52)
    graphite = finish("Handle_and_louvers", (.085,.115,.115), .33,.49)
    tape = finish("Ochre_traffic_coating", (.52,.33,.067), .02,.63)
    back = 2.52
    left = -2.84

    def mesh_object(name, verts, faces, mat, at=(0,0,0), bevel=0, key=None):
        data = shapes.get(key)
        if data is None:
            data = bpy.data.meshes.new(name+"_mesh")
            data.from_pydata(verts,[],faces)
            data.update()
            bm=bmesh.new()
            bm.from_mesh(data)
            bmesh.ops.recalc_face_normals(bm,faces=list(bm.faces))
            if bevel:
                bmesh.ops.bevel(bm,geom=list(bm.edges),offset=bevel,segments=2,
                                affect="EDGES",clamp_overlap=True)
            bm.to_mesh(data)
            bm.free()
            data.materials.append(mat)
            if key is not None:
                shapes[key]=data
        obj=bpy.data.objects.new(name,data)
        col.objects.link(obj)
        obj.parent=root
        obj.location=at
        return obj

    def box(name, at, dims, mat, bevel=.0018):
        x,y,z=(v/2 for v in dims)
        verts=[(-x,-y,-z),(x,-y,-z),(x,y,-z),(-x,y,-z),
               (-x,-y,z),(x,-y,z),(x,y,z),(-x,y,z)]
        faces=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
        return mesh_object(name,verts,faces,mat,at,bevel,(dims,mat.name,bevel))

    def pipe(name, a, b, diameter, mat):
        a,b=Vector(a),Vector(b)
        delta=b-a
        radius=diameter/2
        verts=[(radius*cos(i*2*pi/16),radius*sin(i*2*pi/16),z)
               for z in (-delta.length/2,delta.length/2) for i in range(16)]
        faces=[tuple(reversed(range(16))),tuple(range(16,32))]
        faces.extend((i,(i+1)%16,16+(i+1)%16,16+i) for i in range(16))
        obj=mesh_object(name,verts,faces,mat,(a+b)/2,.0005,
                        ("pipe",round(delta.length,6),diameter,mat.name))
        obj.rotation_euler=delta.to_track_quat("Z","Y").to_euler()
        return obj

    # Stepped liners create physically recessed, millimetre-wide panel joints.
    rear_count,rear_pitch=7,1.22
    rear_length=rear_count*rear_pitch
    x_mid=left+rear_length/2
    box("Rear_panel_joint_liner",(x_mid,back+.051,1.83),(rear_length,.075,3.66),joint)
    for i in range(rear_count):
        x=left+(i+.5)*rear_pitch
        box("Impact_resistant_wall_liner",(x,back,.464),(rear_pitch-.005,.042,.928),base)
        box("Upper_wall_panel",(x,back,1.251),(rear_pitch-.005,.039,.642),warm)
        box("Upper_wall_cap",(x,back,3.217),(rear_pitch-.005,.039,.886),warm)
        box("Clerestory_glazing",(x,back+.015,2.171),(rear_pitch-.022,.035,1.172),light,.003)
        box("Clerestory_mullion",(x-rear_pitch*.5,back-.006,2.172),(.034,.076,1.19),trim,.002)
        box("Mullion_gasket",(x-rear_pitch*.5,back-.046,2.172),(.009,.005,1.16),polymer,.0006)
    for name,z,depth,height,mat in (
        ("Coved_baseboard",.065,.065,.13,trim),("Washdown_wall_cap",.93,.055,.017,trim),
        ("Clerestory_sill",1.58,.097,.044,trim),("Clerestory_header",2.77,.065,.036,trim)):
        box(name,(x_mid,back-.005,z),(rear_length,depth,height),mat)

    return_pitch,return_count=1.205,5
    ret_length=return_pitch*return_count
    y_mid=back-ret_length/2
    box("West_return_liner",(left-.048,y_mid,1.80),(.074,ret_length,3.6),joint)
    for i in range(return_count):
        y=back-(i+.5)*return_pitch
        box("West_wainscot",(left,y,.464),(.038,return_pitch-.005,.928),base)
        box("West_painted_panel",(left,y,2.268),(.038,return_pitch-.005,2.664),warm)
    box("West_baseboard",(left+.004,y_mid,.065),(.064,ret_length,.13),trim)
    box("West_wall_cap",(left+.004,y_mid,.93),(.050,ret_length,.017),trim)

    # Small flush service cabinet, off the robot axis. Shallow, folded door.
    cx=.78
    box("Service_enclosure",(cx,back-.077,.754),(.514,.113,.727),cabinet,.008)
    box("Service_door_reveal",(cx,back-.137,.754),(.487,.012,.703),graphite,.004)
    box("Service_door",(cx,back-.148,.754),(.477,.019,.691),cabinet,.006)
    for z in (.507,1.009):
        box("Concealed_hinge",(cx-.24,back-.153,z),(.016,.019,.056),trim,.003)
    box("Quarter_turn_latch",(cx+.168,back-.160,.795),(.027,.008,.063),graphite,.004)
    for z in (.522,.534,.546,.558):
        box("Downward_louver",(cx+.096,back-.158,z),(.115,.013,.0045),trim,.0008)
    pipe("Cable_conduit",(cx,back-.098,.080),(cx,back-.098,.391),.018,trim)
    pipe("Services_drop",(cx-.162,back-.101,1.118),(cx-.162,back-.101,1.474),.018,trim)
    pipe("Services_elbow",(cx-.162,back-.101,1.469),(cx-.113,back-.101,1.518),.018,trim)
    pipe("Horizontal_trunk",(cx-.113,back-.101,1.518),(2.91,back-.101,1.518),.018,trim)
    for x in (cx+.17,cx+.84,cx+1.52):
        box("Conduit_saddle",(x,back-.10,1.517),(.018,.028,.023),trim,.002)

    # A single continuous L-shaped traffic boundary; the operator approach is open.
    # Consistent winding and a 45-degree clipped outside corner avoid overlapping faces.
    w=.042
    outline=[(-1.28,.955),(1.045,.955),(1.125,.875),(1.125,-.86),
             (1.125-w,-.86),(1.125-w,.8575),(1.0275,.955-w),(-1.28,.955-w)]
    verts=[(x,y,.0012) for x,y in outline]
    faces=[(7,6,1,0),(6,5,2,1),(5,4,3,2)]
    mesh_object("Rear_and_right_aisle_boundary",verts,faces,tape)
    return root
