"""Original metrology fixture for the Cell C visual study. Coordinates are metres.

build_part(name, xyz, angle) returns a parent Empty at the underside centre of a
124 x 82 mm bracket, rotated by angle radians around Z. build_gauge() includes
the seated workpiece and returns named points of interest for camera framing.
"""

import math

import bmesh
import bpy
from mathutils import Matrix, Vector


TAU = math.tau
DECK_Z = 0.82
PART_ORIGIN = (0.077, 0.001, 0.882)
CONTACT = (0.108, -0.008, 0.915)


def _collection(name):
    c = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(c)
    return c


def _material(name, color, metal=0.0, rough=0.38):
    name = "Metrology_" + name
    m = bpy.data.materials.get(name)
    if m:
        return m
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1.0)
    m.metallic = metal
    m.roughness = rough
    m.use_nodes = True
    p = m.node_tree.nodes.get("Principled BSDF")
    p.inputs["Base Color"].default_value = (*color, 1.0)
    p.inputs["Metallic"].default_value = metal
    p.inputs["Roughness"].default_value = rough
    return m


def _finishes():
    return {
        "alu": _material("Milled_6082", (.62, .67, .71), .87, .31),
        "cut": _material("Turned_facing", (.69, .72, .76), .89, .24),
        "blast": _material("Vapor_blasted_aluminium", (.32, .37, .41), .72, .46),
        "steel": _material("Ground_tool_steel", (.38, .45, .49), .90, .24),
        "dark": _material("Gunmetal_anodized", (.073, .100, .126), .70, .33),
        "navy": _material("Deep_blue_enamel", (.029, .062, .100), .28, .31),
        "black": _material("Oxide_steel", (.036, .043, .046), .65, .36),
        "rubber": _material("PUR_cable", (.020, .024, .026), .03, .53),
        "brass": _material("Golden_bronze", (.48, .255, .089), .81, .27),
        "orange": _material("Signal_ochre", (.78, .235, .038), .28, .34),
        "white": _material("Ivory_dial", (.76, .79, .73), .06, .47),
        "ink": _material("Scale_ink", (.008, .012, .014), .02, .48),
        "ruby": _material("Synthetic_ruby", (.59, .017, .077), .34, .16),
    }


def _object(name, verts, faces, mat, collection, origin=(0, 0, 0), bevel=0):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    if bevel:
        bm.normal_update()
        edges = [e for e in bm.edges if e.is_manifold and e.calc_face_angle(0) > .36]
        if edges:
            bmesh.ops.bevel(bm, geom=edges, offset=bevel, segments=3,
                            affect='EDGES', clamp_overlap=True)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    obj.location = origin
    if mat:
        mesh.materials.append(mat)
    return obj


def _plate(name, center, size, corner, mat, c, bevel=.00055):
    """Rounded plan contour and a separate physical edge break."""
    x, y, z = size
    contour = []
    for i, (cx, cy) in enumerate(((x/2-corner, y/2-corner), (-x/2+corner, y/2-corner),
                                  (-x/2+corner, -y/2+corner), (x/2-corner, -y/2+corner))):
        for j in range(7):
            a = (i + j / 6) * math.pi / 2
            contour.append((cx + corner * math.cos(a), cy + corner * math.sin(a)))
    n = len(contour)
    v = [(px, py, pz) for pz in (-z/2, z/2) for px, py in contour]
    f = [tuple(reversed(range(n))), tuple(range(n, 2*n))]
    f += [(i, (i+1)%n, (i+1)%n+n, i+n) for i in range(n)]
    return _object(name, v, f, mat, c, center, bevel)


def _turned(name, center, profile, mat, c, count=40, axis="Z", bevel=0):
    """Closed revolved radial/axial cross-section; radii may close at the axis."""
    v, rings, f = [], [], []
    for radius, h in profile:
        if radius <= 1e-10:
            rings.append([len(v)])
            v.append((0, 0, h))
        else:
            rings.append(list(range(len(v), len(v)+count)))
            v.extend((radius*math.cos(i*TAU/count), radius*math.sin(i*TAU/count), h)
                     for i in range(count))
    for lower, upper in zip(rings, rings[1:]+rings[:1]):
        if len(lower) == len(upper) == 1:
            continue
        for i in range(count):
            j = (i+1) % count
            if len(lower) == 1:
                f.append((lower[0], upper[j], upper[i]))
            elif len(upper) == 1:
                f.append((lower[i], lower[j], upper[0]))
            else:
                f.append((lower[i], lower[j], upper[j], upper[i]))
    obj = _object(name, v, f, mat, c, center, bevel)
    if axis == "Y":
        obj.rotation_euler.x = math.pi/2
    elif axis == "X":
        obj.rotation_euler.y = math.pi/2
    # Shade only constant-radius faces smoothly, keeping face rings and parting lines flat.
    if not bevel:
        for polygon in obj.data.polygons:
            if polygon.loop_total == 4 and abs(polygon.normal.z) < .03:
                polygon.use_smooth = True
    return obj


def _cylinder(name, center, radius, length, mat, c, axis="Z", count=40, chamfer=.0004):
    d = min(chamfer, length*.18, radius*.15)
    lo, hi = -length/2, length/2
    section = [(0, lo), (radius-d, lo), (radius, lo+d), (radius, hi-d),
               (radius-d, hi), (0, hi)]
    return _turned(name, center, section, mat, c, count, axis)


def _socket(name, center, radius, mat, c, axis="Z"):
    """Machined socket-head screw with a real inset hexagonal pocket."""
    n = 36
    r, h = radius, radius*.85
    verts = []
    def hex_r(i):
        a = (i*TAU/n + math.pi/6) % (math.pi/3) - math.pi/6
        return radius*.44/math.cos(a)
    for level, rfunc in ((0, lambda i: r*.93), (.12*h, lambda i: r),
                         (.85*h, lambda i: r), (h, lambda i: .87*r),
                         (h, hex_r), (.31*h, hex_r)):
        verts.extend((rfunc(i)*math.cos(i*TAU/n), rfunc(i)*math.sin(i*TAU/n), level)
                     for i in range(n))
    faces = [tuple(reversed(range(n)))]
    for layer in range(5):
        faces.extend((layer*n+i, layer*n+(i+1)%n, (layer+1)*n+(i+1)%n, (layer+1)*n+i)
                     for i in range(n))
    faces.append(tuple(range(5*n, 6*n)))
    obj = _object(name, verts, faces, mat, c, center)
    if axis == 'Y':
        obj.rotation_euler.x = math.pi/2
    if axis == 'X':
        obj.rotation_euler.y = math.pi/2
    return obj


def _cut(base, cutter):
    modifier = base.modifiers.new('Machine pocket', 'BOOLEAN')
    modifier.operation = 'DIFFERENCE'
    modifier.solver = 'EXACT'
    modifier.object = cutter
    dg = bpy.context.evaluated_depsgraph_get()
    old = base.data
    mesh = bpy.data.meshes.new_from_object(base.evaluated_get(dg), depsgraph=dg)
    base.modifiers.remove(modifier)
    base.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)
    cutter_mesh = cutter.data
    bpy.data.objects.remove(cutter, do_unlink=True)
    if cutter_mesh.users == 0:
        bpy.data.meshes.remove(cutter_mesh)


def _bake_machined_faces(obj):
    """Store Blender's evaluated tessellation of drilled faces in the USD mesh.

    Exact booleans can return a large planar n-gon connected to its circular
    bore by a narrow seam. Different consumers triangulate this seam differently.
    Emit the same triangles Blender evaluated, including their surface finishes.
    """
    old = obj.data
    old.calc_loop_triangles()
    triangles = [(tuple(tri.vertices), tri.polygon_index) for tri in old.loop_triangles]
    mesh = bpy.data.meshes.new(obj.name + "_Machined")
    mesh.from_pydata([v.co[:] for v in old.vertices], [], [tri for tri, _ in triangles])
    for material in old.materials:
        mesh.materials.append(material)
    for face, (_, source_id) in zip(mesh.polygons, triangles):
        source = old.polygons[source_id]
        face.material_index = source.material_index
        face.use_smooth = source.use_smooth
    mesh.update()
    obj.data = mesh
    if old.users == 0:
        bpy.data.meshes.remove(old)


def _rib(name, side_x, mat, c):
    yz = [(-.001, .012), (.026, .012), (.026, .039), (.021, .039), (.001, .018)]
    v = [(x, y, z) for x in (side_x-.0028,side_x+.0028) for y,z in yz]
    n = len(yz)
    f = [tuple(reversed(range(n))), tuple(range(n,2*n))]
    f += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    return _object(name, v, f, mat, c, bevel=.0007)


def build_part(name, xyz, angle=0):
    """Build an aluminium inspection bracket with stepped holes; parent origin is underside."""
    m = _finishes()
    c = _collection(name + "_Assembly")
    part = bpy.data.objects.new(name, None)
    c.objects.link(part)
    part.empty_display_size = .018
    base = _plate(name+"_Foot", (0,0,.006), (.124,.082,.012), .011, m["alu"], c, .0008)
    for ix in (-1,1):
        for iy in (-1,1):
            cutter = _turned("Pocket_tool", (ix*.045,iy*.026,0),
                             [(0,-.004),(.004,-.004),(.004,.0065),(.0072,.0065),
                              (.0072,.01165),(.00765,.01215),(.00765,.018),(0,.018)], None, c)
            _cut(base, cutter)
    bx, by = .017, -.009
    _cut(base, _cylinder("Bore_tool", (bx,by,.006), .008, .025, None, c, chamfer=0.00002))
    _bake_machined_faces(base)
    _turned(name+"_FacedDatumBoss", (bx,by,0),
            [(.008,.012),(.022,.012),(.022,.0133),(.0206,.015),(.0206,.0324),
             (.020,.033),(.0086,.033),(.008,.0324)], m["cut"], c, 72)
    # Back lug is drilled across Y. Its straight lower section and arced crown
    # meet one continuous face around the bore, visible from the machine front.
    contour = [(-.024,.012),(.024,.012),(.024,.034)]
    contour.extend((.024*math.cos(i*math.pi/24), .034+.024*math.sin(i*math.pi/24))
                   for i in range(1,25))
    n = len(contour)
    v = [(x-.013,y,z) for y in (.020,.032) for x,z in contour]
    f = [tuple(reversed(range(n))),tuple(range(n,2*n))]
    f += [(i,(i+1)%n,(i+1)%n+n,i+n) for i in range(n)]
    lug = _object(name+"_CrossDrilledEar",v,f,m["alu"],c,bevel=.00085)
    _cut(lug, _cylinder("Eye_tool", (-.013,.026,.035),.012,.031,None,c,'Y',64))
    _bake_machined_faces(lug)
    for x in (-.032,.006):
        _rib(name+"_Web",x,m["alu"],c)
    for child in list(c.objects):
        if child != part:
            child.parent = part
    part.location = xyz
    part.rotation_euler.z = angle
    return part


def _tube(name, coords, radius, mat, c):
    curve = bpy.data.curves.new(name, 'CURVE')
    curve.dimensions='3D'
    curve.resolution_u=12
    curve.bevel_depth=radius
    curve.bevel_resolution=3
    curve.use_fill_caps=True
    line=curve.splines.new('BEZIER')
    line.bezier_points.add(len(coords)-1)
    for b, p in zip(line.bezier_points,coords):
        b.co=p
        b.handle_left_type='AUTO'
        b.handle_right_type='AUTO'
    tube=bpy.data.objects.new(name,curve)
    c.objects.link(tube)
    tube.data.materials.append(mat)
    dg=bpy.context.evaluated_depsgraph_get()
    mesh=bpy.data.meshes.new_from_object(tube.evaluated_get(dg),depsgraph=dg)
    bpy.data.objects.remove(tube,do_unlink=True)
    bpy.data.curves.remove(curve)
    tube=bpy.data.objects.new(name,mesh)
    c.objects.link(tube)
    return tube


def _sphere(name, center, radius, mat, c):
    v=[(0,0,radius)]
    n,r=32,16
    for j in range(1,r):
        a=j*math.pi/r
        v.extend((radius*math.sin(a)*math.cos(i*TAU/n),
                  radius*math.sin(a)*math.sin(i*TAU/n),radius*math.cos(a)) for i in range(n))
    v.append((0,0,-radius))
    f=[(0,1+i,1+(i+1)%n) for i in range(n)]
    for j in range(r-2):
        a=1+j*n
        f.extend((a+i,a+n+i,a+n+(i+1)%n,a+(i+1)%n) for i in range(n))
    a=1+(r-2)*n
    f.extend((len(v)-1,a+(i+1)%n,a+i) for i in range(n))
    obj=_object(name,v,f,mat,c,center)
    for poly in obj.data.polygons: poly.use_smooth=True


def _dial(c, m, x, y, z):
    # Axis Y points out of the face toward the operator (-Y).
    _turned("ComparatorCase", (x,y,z),
            [(0,-.015),(.027,-.015),(.031,-.011),(.031,.007),(.029,.010),(0,.010)],
            m["dark"],c,72,"Y")
    _turned("ScaleBezel", (x,y,z),
            [(.024,.007),(.0305,.007),(.0315,.009),(.0315,.012),(.0308,.013),
             (.0267,.013),(.0262,.012),(.0262,.009)],m["steel"],c,80,"Y")
    fy=y-.0119
    _cylinder("GaugeScale",(x,fy+.0006,z),.02645,.0013,m["white"],c,"Y",80,.00015)
    marks_v,marks_f=[],[]
    for i in range(100):
        a=i*TAU/100
        inside=.0196 if i%10==0 else .0211 if i%5==0 else .0228
        w=.00028 if i%5==0 else .00015
        d=Vector((math.sin(a),0,math.cos(a)))
        t=Vector((math.cos(a),0,-math.sin(a)))
        start=len(marks_v)
        marks_v.extend(tuple(d*rr+t*ww) for rr,ww in ((inside,-w),(inside,w),(.025,w),(.025,-w)))
        marks_f.append(tuple(range(start,start+4)))
    _object("Engraved100Divisions",marks_v,marks_f,m["ink"],c,(x,fy-.0001,z))
    a=-.36
    d=Vector((math.sin(a),0,math.cos(a)))
    t=Vector((math.cos(a),0,-math.sin(a)))
    _object("ComparatorPointer",[tuple(-d*.006-t*.0012),tuple(d*.018-t*.00045),tuple(d*.023),
                                tuple(d*.018+t*.00045),tuple(-d*.006+t*.0012)],[(0,1,2,3,4)],
            m["orange"],c,(x,fy-.00035,z))
    _cylinder("PointerHub",(x,fy-.00095,z),.0017,.0012,m["black"],c,"Y",24,.00015)
    # Apply the instrument's real scale division text as vector geometry, so
    # the markings also survive interchange into Isaac's USD stage.
    for i in range(10):
        a=i*TAU/10
        _face_text(str(i*10), (x+.0178*math.sin(a),fy-.00016,z+.0178*math.cos(a)),
                   .00365,m["ink"],c)
    _face_text("0.01 mm",(x,fy-.00016,z-.0105),.0029,m["ink"],c)


def _face_text(label, xyz, height, mat, c):
    curve=bpy.data.curves.new("PrintedGraduation", "FONT")
    curve.body=label
    curve.align_x='CENTER'
    curve.align_y='CENTER'
    curve.size=height
    curve.resolution_u=5
    curve.materials.append(mat)
    src=bpy.data.objects.new("Scale_"+label,curve)
    c.objects.link(src)
    dg=bpy.context.evaluated_depsgraph_get()
    mesh=bpy.data.meshes.new_from_object(src.evaluated_get(dg),depsgraph=dg)
    bpy.data.objects.remove(src,do_unlink=True)
    bpy.data.curves.remove(curve)
    obj=bpy.data.objects.new("Scale_"+label,mesh)
    c.objects.link(obj)
    obj.location=xyz
    obj.rotation_euler.x=math.pi/2


def build_gauge():
    m=_finishes()
    c=_collection("M01_Inspection_Fixture")
    _plate("Subplate",(.10,.060,.831),(.366,.335,.022),.014,m["dark"],c,.0014)
    for px in (-.059,.259):
        for py in (-.081,.198):
            _socket("Subplate_M8_socket",(px,py,.842),.0066,m["black"],c)
    _plate("Nest_AnodizedBlock",(.077,.001,.8535),(.210,.159,.023),.009,m["navy"],c,.0012)
    _plate("Nest_GroundFace",(.077,.001,.8675),(.198,.148,.005),.005,m["steel"],c,.00045)
    for px,py in ((.019,-.060),(.138,-.060),(.019,.063),(.138,.063)):
        _socket("Nest_CapScrew",(px,py,.870),.0041,m["black"],c)
    for dx,dy in ((-.043,-.012),(.044,-.012),(0,.029)):
        _turned("DatumRest",(.077+dx,.001+dy,.870),
                [(0,0),(.008,0),(.008,.0025),(.006,.003),(.006,.0114),(.0054,.012),(0,.012)],
                m["steel"],c)
    for dx in (-.045,.045):
        _turned("ToolingLocator",(.077+dx,.001+.026,.870),
                [(0,0),(.00475,0),(.00475,.007),(.00365,.008),(.00365,.020),
                 (.00295,.0215),(0,.0215)],m["steel"],c)
    part=build_part("WB01_InspectionBracket",PART_ORIGIN)
    for sign in (-1,1):
        px=.077+sign*.083
        py=-.002 if sign<0 else .009
        _cylinder("ClampHeel",(px,py,.8815),.010,.023,m["dark"],c)
        _plate("HoldDownToe",(px-sign*.012,py,.8975),(.044,.0105,.007),.0032,
               m["dark"],c,.00065)
        _cylinder("FlatSwivelContact",(.077+sign*.055,py,.89435),.0040,.0007,
                  m["brass"],c,count=28,chamfer=.0001)
        _socket("HoldDownScrew",(px,py,.901),.0056,m["cut"],c)
    _plate("ComparatorColumn_Foot",(.107,.170,.854),(.142,.102,.024),.010,m["navy"],c,.0014)
    for dx in (-.052,.052):
        for dy in (-.036,.036):
            _socket("ColumnBase_M8",(.107+dx,.17+dy,.866),.0059,m["black"],c)
    _plate("ComparatorColumn",(.107,.177,.991),(.063,.051,.250),.005,m["navy"],c,.0014)
    _plate("ColumnTop",(.107,.177,1.118),(.067,.055,.005),.004,m["alu"],c,.00065)
    for x in (.087,.127):
        _plate("LinearGuideRail",(x,.148,.990),(.009,.009,.225),.0013,m["steel"],c,.00055)
        for z in (.894,.925,1.051,1.087):
            _socket("GuideFixing",(x,.1435,z),.0025,m["black"],c,'Y')
    _plate("SlidingCarriage",(.107,.139,.984),(.082,.022,.057),.0035,m["blast"],c,.0011)
    for px in (.077,.137):
        for pz in (.965,1.003):
            _socket("CarriageCap",(px,.128,pz),.0037,m["black"],c,'Y')
    _plate("CantileverSpine",(.108,.0605,.978),(.036,.155,.022),.0045,m["navy"],c,.0015)
    for x in (.094,.122):
        _socket("QuillLock",(x,-.012,.989),.0031,m["steel"],c)
    x,y,contact=CONTACT
    _cylinder("GaugeStem_Upper",(x,y,.9905),.006,.026,m["steel"],c)
    _turned("GaugeStem_Collet",(x,y,.9655),
            [(0,-.013),(.005,-.013),(.0065,-.011),(.0065,-.007),(.0054,-.006),
             (.0066,-.004),(.0066,.009),(.005,.011),(0,.011)],m["steel"],c,40)
    _turned("StylusStem",(x,y,contact),
            [(0,.0038),(.0011,.0038),(.0011,.018),(.0025,.020),(.0025,.0375),(0,.0375)],
            m["steel"],c,32)
    _sphere("RubyContactBall",(x,y,contact+.00225),.00225,m["ruby"],c)
    _dial(c,m,x,y,1.020)
    _cylinder("GaugeLiftPlunger",(x,y,1.057),.0036,.017,m["steel"],c)
    _cylinder("GaugeLiftThumb",(x,y,1.066),.0075,.005,m["black"],c)
    _cylinder("GaugeConnector",(x+.032,y+.004,1.020),.0049,.012,m["brass"],c,"X",32)
    _tube("SignalCable",[(x+.037,y+.004,1.020),(.165,.007,1.024),(.190,.032,1.056),
                          (.197,.103,1.067),(.178,.153,1.034),(.161,.190,.987),
                          (.162,.192,.914),(.174,.204,.852)],.00235,m["rubber"],c)
    _cylinder("CableGland",(.174,.204,.848),.0056,.012,m["black"],c)
    _cylinder("CrossLockHub",(.151,.140,.983),.009,.016,m["steel"],c,"X")
    _turned("CrossLockKnob",(.173,.14,.983),
            [(0,-.017),(.011,-.017),(.016,-.013),(.017,-.010),(.017,.008),
             (.014,.011),(0,.011)],m["black"],c,48,"X")
    for i in range(16):
        a=i*TAU/16
        _cylinder("KnobKnurl",(.172,.14+.016*math.cos(a),.983+.016*math.sin(a)),
                  .00125,.017,m["black"],c,"X",12,.00025)
    return {"contact": CONTACT,"dial": (x,y,1.020),"part": part,"fixture": c}
