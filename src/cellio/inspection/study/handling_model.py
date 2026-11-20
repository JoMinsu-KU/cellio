"""Indexed conveyor and two routing trays. Dimensions are metres, world Z is up."""

from math import cos, pi, radians, sin

import bmesh
import bpy
from mathutils import Vector


def material(name, color, metal, rough):
    name = "Handling_" + name
    mat = bpy.data.materials.get(name)
    if mat:
        return mat
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*color, 1)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*color, 1)
    bsdf.inputs["Metallic"].default_value = metal
    bsdf.inputs["Roughness"].default_value = rough
    return mat


class Fabrication:
    def __init__(self):
        self.collection = bpy.data.collections.new("Part_handling")
        bpy.context.scene.collection.children.link(self.collection)
        self.meshes = {}
        self.root = self.object("Part_handling", None, (0, 0, 0), None)
        self.parent = self.root
        self.silver = material("Clear_anodized", (.41, .48, .51), .78, .31)
        self.sheet = material("Folded_stainless", (.38, .42, .44), .84, .34)
        self.nickel = material("Machined_nickel", (.54, .59, .60), .88, .25)
        self.dark = material("Black_anodized", (.032, .045, .055), .69, .38)
        self.belt = material("Graphite_urethane", (.046, .060, .063), .03, .61)
        self.band = material("Vulcanized_joint", (.060, .073, .074), .04, .68)
        self.plastic = material("POM_nest", (.054, .066, .070), .02, .39)
        self.rubber = material("EPDM", (.015, .019, .021), .01, .66)
        self.ok = material("Teal_enamel", (.025, .29, .185), .21, .34)
        self.ng = material("Vermilion_enamel", (.51, .075, .028), .19, .34)
        self.white = material("White_legend", (.87, .89, .86), .08, .36)
        self.amber = material("Amber_optics", (.85, .31, .026), .18, .21)

    def object(self, name, mesh, position, parent, rotation=(0, 0, 0)):
        obj = bpy.data.objects.new(name, mesh)
        self.collection.objects.link(obj)
        obj.parent = parent
        obj.location = position
        obj.rotation_euler = rotation
        if mesh is None:
            obj.empty_display_type = "PLAIN_AXES"
            obj.empty_display_size = .03
        return obj

    def assembly(self, name):
        self.parent = self.object(name, None, (0, 0, 0), self.root)

    def geometry(self, name, verts, faces, mat, position=(0, 0, 0), bevel=0,
                 rotation=(0, 0, 0), cache=None):
        key = None if cache is None else (cache, mat.name, bevel)
        mesh = self.meshes.get(key) if key is not None else None
        if mesh is None:
            mesh = bpy.data.meshes.new(name + "_mesh")
            mesh.from_pydata(verts, [], faces)
            mesh.update()
            bm = bmesh.new()
            bm.from_mesh(mesh)
            bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
            if bevel:
                bmesh.ops.bevel(bm, geom=list(bm.edges), offset=bevel, segments=3,
                                affect="EDGES", clamp_overlap=True)
            bm.normal_update()
            bm.to_mesh(mesh)
            bm.free()
            mesh.materials.append(mat)
            if key is not None:
                self.meshes[key] = mesh
        return self.object(name, mesh, position, self.parent, rotation)

    def box(self, name, xyz, dims, mat, bevel=.0005, rotation=(0, 0, 0)):
        a, b, c = (v / 2 for v in dims)
        verts = [(-a,-b,-c),(a,-b,-c),(a,b,-c),(-a,b,-c),
                 (-a,-b,c),(a,-b,c),(a,b,c),(-a,b,c)]
        faces = [(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
        return self.geometry(name, verts, faces, mat, xyz, bevel, rotation, ("box", dims))

    def revolved(self, name, xyz, profile, mat, axis="Z", n=32):
        """A radial profile ordered around the section, including hollow sections."""
        verts, faces, rings = [], [], []
        for radius, z in profile:
            ring = []
            if radius == 0:
                ring.append(len(verts))
                verts.append((0, 0, z))
            else:
                for k in range(n):
                    theta = 2 * pi * k / n + pi / n
                    ring.append(len(verts))
                    verts.append((radius * cos(theta), radius * sin(theta), z))
            rings.append(ring)
        for a, b in zip(rings, rings[1:] + rings[:1]):
            if len(a) == len(b) == 1:
                continue
            for k in range(n):
                j = (k + 1) % n
                if len(a) == 1:
                    faces.append((a[0], b[k], b[j]))
                elif len(b) == 1:
                    faces.append((a[j], a[k], b[0]))
                else:
                    faces.append((a[k], a[j], b[j], b[k]))
        rot = {"Z": (0,0,0), "Y": (-pi/2,0,0), "-Y": (pi/2,0,0), "X": (0,pi/2,0)}[axis]
        return self.geometry(name, verts, faces, mat, xyz, rotation=rot,
                             cache=("revolve", tuple(profile), n))

    def drum(self, name, xyz, r, length, mat, axis="Z", n=32):
        h = length / 2
        fillet = min(.0007, h * .45, r * .1)
        profile = [(0,-h),(r-fillet,-h),(r,-h+fillet),(r,h-fillet),(r-fillet,h),(0,h)]
        return self.revolved(name, xyz, profile, mat, axis, n)

    def screw(self, name, xyz, axis="Z", r=.0036):
        # The hexagonal socket is a real cavity with walls, not a painted dot.
        rings = [(r*.96, 0, 24),(r,.0005,24),(r,.0020,24),
                 (r*.87,.00245,24),(r*.44,.00245,6),(r*.44,.00075,6)]
        vs, fs = [], []
        for rad, z, count in rings:
            for k in range(count):
                ang = 2*pi*k/count
                vs.append((rad*cos(ang),rad*sin(ang),z))
        fs.append(tuple(reversed(range(24))))
        for start in (0,24,48):
            fs.extend((start+k,start+(k+1)%24,start+24+(k+1)%24,start+24+k) for k in range(24))
        for k in range(6):
            first = 4*k
            outer = [72+(first+j)%24 for j in range(5)]
            # Triangulating the narrow annulus keeps the hex cavity visible when exported.
            fs.extend((outer[j],outer[j+1],96+k) for j in range(4))
            fs.append((outer[-1],96+(k+1)%6,96+k))
            fs.append((96+k,96+(k+1)%6,102+(k+1)%6,102+k))
        fs.append(tuple(range(102,108)))
        rotation = {"Z":(0,0,0),"-Y":(pi/2,0,0),"Y":(-pi/2,0,0),"X":(0,pi/2,0)}[axis]
        return self.geometry(name,vs,fs,self.nickel,xyz,rotation=rotation,cache=("screw",r))

    def cable(self, name, pts, radius=.002):
        curve = bpy.data.curves.new(name + "_curve", "CURVE")
        curve.dimensions = "3D"
        curve.resolution_u = 6
        curve.bevel_depth = radius
        curve.bevel_resolution = 2
        curve.use_fill_caps = True
        sp = curve.splines.new("BEZIER")
        sp.bezier_points.add(len(pts)-1)
        for pt, xyz in zip(sp.bezier_points, pts):
            pt.co = xyz
            pt.handle_left_type = pt.handle_right_type = "AUTO"
        obj = self.object(name,curve,(0,0,0),self.parent)
        self.bake_curve(obj, self.rubber)

    def bake_curve(self, obj, mat):
        curve = obj.data
        graph = bpy.context.evaluated_depsgraph_get()
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(graph), depsgraph=graph)
        name = obj.name
        pos, rot = tuple(obj.location), tuple(obj.rotation_euler)
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.curves.remove(curve)
        mesh.materials.append(mat)
        return self.object(name, mesh, pos, self.parent, rot)

    def legend(self, text, xyz, slope):
        font = bpy.data.curves.new("Tray_legend_" + text, "FONT")
        font.body = text
        font.align_x, font.align_y = "CENTER", "CENTER"
        font.size = .017
        font.extrude = .00010
        font.bevel_depth = .00004
        font.space_character = 1.15
        obj = self.object("Marker_"+text, font, xyz, self.parent, (slope,0,0))
        return self.bake_curve(obj,self.white)


def belt_loop(f, cx, cy, cz):
    verts, faces = [], []
    for radius in (.023, .021):
        for y in (-.056,.056):
            for side, start in ((1, -pi/2),(-1,pi/2)):
                for i in range(17):
                    angle = start+pi*i/16
                    verts.append((side*.199+radius*cos(angle),y,radius*sin(angle)))
    n = 34
    for i in range(n):
        j=(i+1)%n
        faces.extend([(i,j,n+j,n+i),(2*n+i,3*n+i,3*n+j,2*n+j),
                      (i,2*n+i,2*n+j,j),(n+i,n+j,3*n+j,3*n+i)])
    f.geometry("Formed_endless_belt",verts,faces,f.belt,(cx,cy,cz))


def conveyor(f):
    f.assembly("Indexed_infeed")
    cx,cy,cz = -.425,-.248,.876
    belt_loop(f,cx,cy,cz)
    for side in (-1,1):
        y = cy+side*.068
        f.box("Anodized_side_frame",(cx,y,.858),(.473,.012,.045),f.silver,.0018)
        f.box("T_slot_inlay",(cx,y+side*.0061,.857),(.451,.0003,.0033), f.dark,.00012)
        f.box("Belt_edge_rail",(cx,y,.883),(.456,.010,.008),f.silver,.001)
        for delta_x in (-.153,.153):
            x = cx+delta_x
            f.box("Bracket_sole",(x,y+side*.006,.823),(.043,.044,.006),f.silver)
            f.box("Bracket_upright",(x,y,.835),(.024,.014,.020),f.dark)
            f.screw("Foot_fastener",(x,y+side*.018,.826))
        for delta_x in (-.165,.127):
            x = cx+delta_x
            f.box("Side_guide_standoff",(x,y,.8915),(.012,.009,.022),f.dark,.0008)
            f.screw("Rail_adjuster",(x,y+side*.005,.895),"-Y" if side<0 else "Y",.0028)
        f.box("Part_guide",(cx-.014,cy+side*.0525,.905),(.344,.0045,.009),f.silver,.0008)
        for k in range(21):
            f.box("Edge_tracking_mark",(cx-.180+k*.018,cy+side*.049,.89901),
                  (.0009,.007,.00010),f.band,.00003)
    for delta_x in (-.199,.199):
        x=cx+delta_x
        f.drum("Belt_pulley",(x,cy,cz),.021,.124,f.silver,"Y")
        for sy in (-1,1):
            orient="-Y" if sy<0 else "Y"
            f.drum("Flanged_bearing",(x,cy+sy*.078,cz),.014,.006,f.dark,orient)
            f.drum("Bearing_race",(x,cy+sy*.082,cz),.009,.003,f.nickel,orient)
            f.screw("Pulley_bolt",(x,cy+sy*.084,cz),orient,.0052)
    # A directly coupled geared drive. The fan ribs run axially along the motor.
    motor_x, motor_z = cx-.199,.858
    f.box("Worm_reducer",(motor_x,cy+.089,.866),(.059,.029,.050),f.dark,.0035)
    f.drum("Drive_motor",(motor_x,cy+.119,motor_z),.020,.034,f.dark,"Y")
    for i in range(10):
        ang=i*2*pi/10
        f.box("Motor_cooling_fin",(motor_x+.020*cos(ang),cy+.118,motor_z+.020*sin(ang)),
              (.003,.031,.003),f.silver,.0008)
    f.drum("Motor_end_cap",(motor_x,cy+.137,motor_z),.021,.005,f.dark,"Y")
    f.cable("Motor_power_lead",[(motor_x+.018,cy+.11,motor_z),
                                (motor_x+.045,cy+.119,.853),(motor_x+.052,cy+.120,.83),
                                (motor_x+.112,cy+.114,.824),(motor_x+.156,cy+.114,.824)],.0028)
    # A retroreflective optical gate straddles the outfeed, clear of the payload.
    px = cx+.165
    for side in (-1,1):
        py = cy+side*.072
        f.box("Photoeye_mount",(px,py,.900),(.021,.005,.048),f.silver,.0009)
        f.box("Sensor_or_reflector",(px,py-side*.005,.923),(.021,.010,.019),
              f.dark if side<0 else f.silver,.0013)
        f.drum("Optical_face",(px,py-side*.0106,.923),.006,.0018,
               f.amber if side<0 else f.white,"Y",24)
    f.cable("Photoeye_lead",[(px,cy-.081,.919),(px+.011,cy-.089,.906),
                             (px+.014,cy-.089,.880),(px-.018,cy-.084,.873),
                             (px-.048,cy-.078,.865)],.0016)


def side_cheek(f, name, x, y):
    # Folded side cheeks rise towards the rear and expose a thumb scoop at the nest.
    bottom = .834
    silhouette=[(-.107,0),(.107,0),(.107,.053),(.081,.053),(.062,.051),
                (.050,.043),(.035,.026),(.026,.023),(-.023,.023),(-.035,.026),
                (-.046,.039),(-.055,.042),(-.101,.034),(-.107,.028)]
    verts=[(dx,dy,z) for dx in (-.0013,.0013) for dy,z in silhouette]
    n=len(silhouette)
    faces=[tuple(reversed(range(n))),tuple(range(n,2*n))]
    faces.extend((i,(i+1)%n,n+(i+1)%n,n+i) for i in range(n))
    f.geometry(name,verts,faces,f.sheet,(x,y,bottom),.00055,cache="cheek")


def tray(f, name, y, accent):
    f.assembly("Routing_tray_"+name)
    x=.50
    for dx in (-.116,.116):
        for dy in (-.084,.084):
            f.drum("Isolation_foot",(x+dx,y+dy,.826),.010,.012,f.rubber)
    f.box("Tray_flange",(x,y,.835),(.281,.218,.006),f.sheet,.002)
    for sx in (-1,1):
        side_cheek(f,"Rolled_side_panel",x+sx*.138,y)
        for sy in (-1,1):
            f.screw("Tray_mount",(x+sx*.119,y+sy*.084,.838))
    f.box("Rear_wall",(x,y+.105,.859),(.276,.003,.050),f.sheet,.001)
    f.box("Rear_stiffened_edge",(x,y+.106,.885),(.280,.0065,.0042),accent,.001)
    f.box("Front_wall",(x,y-.105,.851),(.276,.003,.030),f.sheet,.001)
    f.box("Front_stiffened_edge",(x,y-.105,.867),(.279,.006,.0036),accent,.0008)
    f.box("Removable_nest",(x,y,.846),(.212,.153,.016),f.plastic,.004)
    # Four low, open guides leave a clear 136 x 96 mm seat at Z .854.
    for sx in (-1,1):
        for sy in (-1,1):
            f.box("Corner_guide_return",(x+sx*.071,y+sy*.040,.859),
                  (.006,.020,.010),f.silver,.0014)
            f.box("Corner_guide_fence",(x+sx*.063,y+sy*.051,.859),
                  (.022,.006,.010),f.silver,.0014)
            f.screw("Nest_fastener",(x+sx*.087,y+sy*.059,.854),r=.0031)
    slope=radians(62)
    ctr=Vector((x,y-.109,.855))
    normal=Vector((0,-sin(slope),cos(slope)))
    f.box("Engraved_plate_"+name,tuple(ctr),(.061,.025,.0028),accent,.0012,(slope,0,0))
    f.legend(name,tuple(ctr+normal*.0015),slope)


def build_handling():
    """Build the presentation meshes. Part underside Z=.899 infeed, .854 in both nests."""
    f=Fabrication()
    conveyor(f)
    tray(f,"OK",-.19,f.ok)
    tray(f,"NG",.16,f.ng)
    return f.root
