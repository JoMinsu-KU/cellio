"""Original metrology bench and operator hardware, with dimensions in metres.

Call build_skid() in Blender. The deck's machined top is at Z=0.820.
"""

from math import atan2, cos, pi, sin

import bpy
from mathutils import Vector


def build_skid():
    collection = bpy.data.collections.new("Cell C | inspection bench")
    bpy.context.scene.collection.children.link(collection)

    def group(name, parent=None):
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "PLAIN_AXES"
        obj.empty_display_size = 0.035
        collection.objects.link(obj)
        obj.parent = parent
        return obj

    root = group("InspectionSkid")
    frame = group("StructuralFrame", root)
    housing = group("ElectricalEnclosure", root)
    console = group("OperatorControls", root)
    tower = group("SignalTower", root)
    harness = group("CableManagement", root)

    def material(name, rgb, metallic, roughness, glow=0):
        mat = bpy.data.materials.new("Skid / " + name)
        mat.use_nodes = True
        mat.diffuse_color = (*rgb, 1)
        shader = mat.node_tree.nodes.get("Principled BSDF")
        shader.inputs["Base Color"].default_value = (*rgb, 1)
        shader.inputs["Metallic"].default_value = metallic
        shader.inputs["Roughness"].default_value = roughness
        if glow:
            shader.inputs["Emission Color"].default_value = (*rgb, 1)
            shader.inputs["Emission Strength"].default_value = glow
        return mat

    aluminum = material("Clear anodized structural profile", (.38, .42, .45), .78, .33)
    deck = material("Hard anodized tooling plate", (.185, .21, .22), .78, .36)
    cut = material("Recessed machining", (.075, .085, .088), .74, .43)
    steel = material("Zinc plated hardware", (.48, .49, .48), .9, .28)
    stainless = material("Brushed stainless", (.51, .53, .53), .84, .32)
    graphite = material("Graphite powder coat", (.052, .064, .075), .38, .41)
    navy = material("Deep blue powder coat", (.053, .108, .165), .32, .38)
    door = material("Cabinet door enamel", (.104, .181, .245), .29, .40)
    rubber = material("Rubber and gaskets", (.016, .021, .024), .03, .58)
    socket = material("Socket recess", (.028, .032, .035), .63, .43)
    yellow = material("Emergency control bezel", (.88, .57, .014), .1, .34)
    red = material("Emergency stop", (.67, .027, .012), .09, .33)
    green = material("Green indicator on", (.022, .59, .17), .08, .23, .4)
    blue = material("Reset button", (.024, .18, .43), .11, .31)
    amber_off = material("Amber lens unlit", (.35, .14, .022), .16, .29)
    red_off = material("Red lens unlit", (.28, .023, .019), .16, .28)
    legend = material("Printed control legends", (.78, .83, .80), .04, .49)

    mesh_cache = {}

    def obj_with_mesh(name, mesh, location, parent, direction=None, edge=0):
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        obj.parent = parent
        obj.location = location
        if direction is not None:
            obj.rotation_mode = 'QUATERNION'
            obj.rotation_quaternion = Vector(direction).to_track_quat('Z', 'Y')
        if edge:
            bevel = obj.modifiers.new("Edge break", 'BEVEL')
            bevel.width = edge
            bevel.segments = 3
            bevel.limit_method = 'ANGLE'
            bevel.harden_normals = True
            weighted = obj.modifiers.new("Face-weighted normals", 'WEIGHTED_NORMAL')
            weighted.keep_sharp = True
        return obj

    def polygon_mesh(name, verts, faces, materials, face_materials=()):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(verts, [], faces)
        for mat in materials:
            mesh.materials.append(mat)
        for poly, idx in zip(mesh.polygons, face_materials):
            poly.material_index = idx
        mesh.update()
        return mesh

    def cuboid(name, center, size, mat, parent, edge=.001):
        key = ('box', tuple(size), mat.name)
        if key not in mesh_cache:
            a, b, c = (v / 2 for v in size)
            verts = [(-a,-b,-c), (a,-b,-c), (a,b,-c), (-a,b,-c),
                     (-a,-b,c), (a,-b,c), (a,b,c), (-a,b,c)]
            faces = [(3,2,1,0),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
            mesh_cache[key] = polygon_mesh(name, verts, faces, [mat])
        return obj_with_mesh(name, mesh_cache[key], center, parent, edge=edge)

    def round_part(name, center, radius, height, mat, parent, direction=(0,0,1), sides=32, edge=.0006):
        key = ('cylinder', radius, height, sides, mat.name)
        if key not in mesh_cache:
            verts = [(radius*cos(2*pi*i/sides), radius*sin(2*pi*i/sides), z)
                     for z in (-height/2, height/2) for i in range(sides)]
            faces = [tuple(range(sides-1,-1,-1)), tuple(range(sides,2*sides))]
            faces += [(i, (i+1)%sides, sides+(i+1)%sides, sides+i) for i in range(sides)]
            mesh = polygon_mesh(name, verts, faces, [mat])
            for p in list(mesh.polygons)[2:]:
                p.use_smooth = True
            mesh_cache[key] = mesh
        return obj_with_mesh(name, mesh_cache[key], center, parent, direction, edge=edge)

    def cap_screw(name, surface, parent, direction=(0,0,1), radius=.0055):
        """Socket-head cap with a genuine inset hex cavity; surface is underside of head."""
        key = ('socket-head', radius)
        if key not in mesh_cache:
            verts, faces, indices = [], [], []
            n = 24
            rings = ((radius*.92, 0), (radius, radius*.13), (radius, radius*.64),
                     (radius*.89,radius*.78), (radius*.48,radius*.78), (radius*.46,radius*.37))
            for ring,(r,z) in enumerate(rings):
                for i in range(n):
                    a = 2*pi*i/n
                    if ring >= 4:
                        # Radial intersection with a regular hexagon with a corner at angle zero.
                        r_local = r*cos(pi/6)/cos((a % (pi/3)) - pi/6)
                    else:
                        r_local = r
                    verts.append((r_local*cos(a), r_local*sin(a), z))
            for j in range(len(rings)-1):
                for i in range(n):
                    k = (i+1)%n
                    faces.append((j*n+i,j*n+k,(j+1)*n+k,(j+1)*n+i))
                    indices.append(1 if j >= 4 else 0)
            faces.append(tuple(range(5*n,6*n)))
            indices.append(1)
            mesh_cache[key] = polygon_mesh(name, verts, faces, [steel,socket], indices)
        return obj_with_mesh(name, mesh_cache[key], surface, parent, direction)

    def extrusion(name, start, end):
        # 40-series rail cross section. The four open keyholes run the length of each member.
        start, end = Vector(start), Vector(end)
        delta = end - start
        length = round(delta.length,6)
        key = ('extrusion',length)
        if key not in mesh_cache:
            edge = [(-.0185,-.020),(-.0043,-.020),(-.0043,-.0175),(-.0076,-.0175),
                    (-.0076,-.014),(-.0045,-.0105),(.0045,-.0105),(.0076,-.014),
                    (.0076,-.0175),(.0043,-.0175),(.0043,-.020),(.0185,-.020)]
            outline = []
            for q in range(4):
                for x,y in edge:
                    outline.append(((x,y),(-y,x),(-x,-y),(y,-x))[q])
            n = len(outline)
            vs = [(x,y,z) for z in (-length/2,length/2) for x,y in outline]
            fs = [tuple(range(n-1,-1,-1)),tuple(range(n,2*n))]
            fs += [(i,(i+1)%n,n+(i+1)%n,n+i) for i in range(n)]
            ids = [0,0]+[(1 if i%12 in (3,4,5,6,7) else 0) for i in range(n)]
            mesh_cache[key] = polygon_mesh(name,vs,fs,[aluminum,cut],ids)
        return obj_with_mesh(name,mesh_cache[key],(start+end)/2,frame,delta)

    # Upper support, lower stretcher rails and fine-thread levelling mounts.
    for x in (-.71,.71):
        for y in (-.405,.405):
            extrusion("40x40 upright",(x,y,.082),(x,y,.792))
            round_part("Vibration pad",(x,y,.011),.044,.022,rubber,frame,edge=.002)
            round_part("Foot disc",(x,y,.025),.037,.011,graphite,frame)
            round_part("Adjusting spindle",(x,y,.056),.009,.055,steel,frame)
            round_part("Lock nut",(x,y,.066),.016,.012,stainless,frame,sides=6)
            cuboid("Threaded foot insert",(x,y,.083),(.039,.039,.008),graphite,frame)
        for z in (.768,.223):
            extrusion("Depth rail",(x,-.385,z),(x,.385,z))
    for y in (-.405,.405):
        for z in (.768,.223):
            extrusion("Front or rear rail",(-.69,y,z),(.69,y,z))
    for x in (-.38,.16,.45):
        extrusion("Deck crossmember",(x,-.385,.768),(x,.385,.768))
    # Powder-coated corner hardware bolted into the forward and side rails.
    for sign in (-1,1):
        x = sign*.71
        for z in (.223,.768):
            cuboid("Junction splice",(x-sign*.034,-.427,z),(.106,.005,.035),graphite,frame,.0025)
            for dx in (0,-sign*.066):
                cap_screw("M6 front rail bolt",(x+dx,-.4295,z),frame,(0,-1,0))
        for y in (-.405,.405):
            cuboid("Upright angle side plate",(x+sign*.022,y,.738),(.005,.034,.103),graphite,frame,.002)
            for z in (.707,.765):
                cap_screw("Side splice bolt",(x+sign*.0245,y,z),frame,(sign,0,0))

    # Construct the top and blind tapped bores in a single mesh. Every cell has
    # a circular mouth and countersink; the hole wall and floor sit below the datum.
    top = .820
    xs = [-.78]+[-.65+.1*i for i in range(14)]+[.78]
    ys = [-.48]+[-.35+.1*i for i in range(8)]+[.48]
    verts, faces, mids, lookup = [], [], [], {}
    def vertex(x,y,z):
        key = (round(x,7),round(y,7),round(z,7))
        if key not in lookup:
            lookup[key] = len(verts)
            verts.append(key)
        return lookup[key]
    def face(v, mat=0):
        faces.append(v)
        mids.append(mat)
    for ix in range(len(xs)-1):
        for iy in range(len(ys)-1):
            x0,x1,y0,y1=xs[ix],xs[ix+1],ys[iy],ys[iy+1]
            cx,cy=(x0+x1)/2,(y0+y1)/2
            # Include corners and two evenly spaced samples on each cell edge.
            outer=[(x1,cy),(x1,cy+(y1-cy)*.5),(x1,y1),(cx+(x1-cx)*.5,y1),
                   (cx,y1),(cx-(cx-x0)*.5,y1),(x0,y1),(x0,cy+(y1-cy)*.5),
                   (x0,cy),(x0,cy-(cy-y0)*.5),(x0,y0),(cx-(cx-x0)*.5,y0),
                   (cx,y0),(cx+(x1-cx)*.5,y0),(x1,y0),(x1,cy-(cy-y0)*.5)]
            outside = [vertex(x,y,top) for x,y in outer]
            # Keep the unbored outer margin on all four sides.
            if ix in (0,len(xs)-2) or iy in (0,len(ys)-2):
                face(tuple(outside))
                continue
            n=len(outer)
            ring=[]
            for radius,z in ((.0049,top),(.0041,top-.0008),(.0041,top-.009)):
                ring.append([vertex(cx+radius*cos(2*pi*i/n),cy+radius*sin(2*pi*i/n),z) for i in range(n)])
            layers=[outside,*ring]
            for j in range(3):
                for i in range(n):
                    k=(i+1)%n
                    face((layers[j][i],layers[j][k],layers[j+1][k],layers[j+1][i]),0 if j==0 else 1)
            face(tuple(ring[-1]),1)
    boundary=sorted(((k,idx) for k,idx in lookup.items() if k[2]==top and
                     (abs(abs(k[0])-.78)<1e-6 or abs(abs(k[1])-.48)<1e-6)),
                    key=lambda pair: atan2(pair[0][1],pair[0][0]))
    bottom=[vertex(x,y,.794) for (x,y,_),_idx in boundary]
    for i,(_co,idx) in enumerate(boundary):
        k=(i+1)%len(boundary)
        face((idx,bottom[i],bottom[k],boundary[k][1]),0)
    face(tuple(reversed(bottom)),0)
    obj_with_mesh("M8 tooling plate - 100 mm pitch",polygon_mesh("Tapped metrology deck",verts,faces,[deck,cut],mids),(0,0,0),frame)
    for y in (-.42,.42):
        cuboid("Table isolation strip",(0,y,.791),(1.49,.036,.006),rubber,frame,.001)

    # Sealed low-voltage control cabinet supported by bottom rails.
    cuboid("Cabinet shell",(0,.025,.497),(1.047,.654,.466),navy,housing,.014)
    for x in (-.385,.385):
        cuboid("Cabinet mounting runner",(x,0,.25),(.05,.78,.03),graphite,housing,.003)
    for x in (-.255,.255):
        cuboid("Door black perimeter gasket",(x,-.309,.502),(.49,.013,.432),rubber,housing,.005)
        cuboid("Formed cabinet door",(x,-.32,.502),(.483,.014,.425),door,housing,.005)
    for x in (-.481,.481):
        for z in (.358,.643):
            cuboid("External hinge leaf",(x,-.33,z),(.034,.009,.05),graphite,housing,.003)
            round_part("Hinge pin",(x,-.337,z),.0055,.046,stainless,housing)
    for x in (-.038,.038):
        cuboid("Flush cam latch",(x,-.33,.564),(.024,.009,.077),graphite,housing,.004)
        cuboid("Swing handle",(x,-.34,.563),(.009,.012,.061),stainless,housing,.003)
    for x in (-.31,.31):
        for j in range(8):
            z=.371+j*.011
            cuboid("Vent dark slot",(x,-.328,z),(.172,.002,.0038),rubber,housing,.0007)
            blade=cuboid("Formed vent louvre",(x,-.331,z+.0027),(.178,.007,.0028),door,housing,.0008)
            blade.rotation_euler[0]=-.25

    def cable(name, points, rad, mat):
        curve=bpy.data.curves.new(name,'CURVE')
        curve.dimensions='3D'
        curve.resolution_u=10
        curve.bevel_depth=rad
        curve.bevel_resolution=2
        spline=curve.splines.new('BEZIER')
        spline.bezier_points.add(len(points)-1)
        for pt,co in zip(spline.bezier_points,points):
            pt.co=co
            pt.handle_left_type=pt.handle_right_type='AUTO'
        obj=bpy.data.objects.new(name,curve)
        collection.objects.link(obj)
        obj.parent=harness
        curve.materials.append(mat)
        deps=bpy.context.evaluated_depsgraph_get()
        mesh=bpy.data.meshes.new_from_object(obj.evaluated_get(deps))
        baked=obj_with_mesh(name+" hose",mesh,(0,0,0),harness)
        bpy.data.objects.remove(obj,do_unlink=True)
        return baked

    # The machined adapter's reference face carries the native arm at Z=0.832.
    cuboid("Robot anchor adapter",(-.21,.17,.826),(.220,.220,.012),graphite,frame,.003)
    for dx in (-.092,.092):
        for dy in (-.092,.092):
            cap_screw("Robot adapter anchor",(-.21+dx,.17+dy,.832),frame,radius=.0068)

    # The umbilical leaves the robot horizontally, then enters the deck grommet vertically.
    round_part("Robot port gland",(-.288,.17,.868),.014,.017,graphite,harness,(-1,0,0),sides=6)
    round_part("Robot connector ferrule",(-.299,.17,.868),.011,.014,stainless,harness,(-1,0,0))
    for i,r in enumerate((.0108,.0101,.0096,.0088)):
        round_part("Robot lead bend relief",(-.308-.0033*i,.17,.868),r,.0031,rubber,harness,(-1,0,0))
    cable("Robot service sleeve",[(-.315,.17,.868),(-.331,.171,.867),(-.351,.185,.859),
          (-.373,.225,.845),(-.391,.274,.844),(-.4,.307,.843),(-.4,.320,.822)],
          .0072,rubber)
    n=40
    rings=[(.009,.8198),(.018,.8198),(.018,.8213),(.0155,.8238),(.009,.8238)]
    ring_vertices=[(r*cos(2*pi*i/n),r*sin(2*pi*i/n),z-.82) for r,z in rings for i in range(n)]
    ring_faces=[(j*n+i,j*n+(i+1)%n,(j+1)*n+(i+1)%n,(j+1)*n+i)
                for j in range(len(rings)-1) for i in range(n)]
    grommet=polygon_mesh("Flanged cable grommet",ring_vertices,ring_faces,[rubber])
    obj_with_mesh("Robot umbilical deck grommet",grommet,(-.4,.32,.82),harness)
    round_part("Grommet throat",(-.4,.32,.8204),.0091,.0005,socket,harness,edge=0)

    for i in range(2):
        y=.19+i*.065
        round_part("Gland locknut",(.528,y,.47),.018,.012,graphite,housing,(1,0,0),sides=6)
        round_part("Cable gland",(.545,y,.47),.014,.029,rubber,housing,(1,0,0))
        cable("Jacketed cabinet lead",[(.553,y,.47),(.595,y,.47),(.623,y,.487),
              (.644,y,.547),(.647,y,.71),(.62,y,.765)],.0085,rubber)
        cuboid("Lead saddle",(.652,y,.62),(.014,.036,.013),graphite,harness,.002)

    # Local operator station sits BELOW the front edge, out of the part flow.
    cuboid("Operator bracket",(.475,-.405,.686),(.206,.043,.124),graphite,console,.005)
    cuboid("Button enclosure",(.475,-.457,.687),(.252,.067,.115),graphite,console,.009)
    cuboid("Button faceplate",(.475,-.493,.687),(.243,.007,.106),stainless,console,.004)
    for x,mat,name in ((.398,green,"Start"),(.47,blue,"Reset"),(.55,red,"Emergency stop")):
        if name=="Emergency stop":
            round_part("E-stop yellow backing",(x,-.498,.698),.031,.003,yellow,console,(0,-1,0))
            round_part("E-stop base",(x,-.51,.698),.019,.02,graphite,console,(0,-1,0))
            round_part("Latching mushroom",(x,-.527,.698),.028,.02,red,console,(0,-1,0),edge=.003)
        else:
            round_part("Switch chrome collar",(x,-.499,.698),.016,.004,steel,console,(0,-1,0))
            round_part("Button rubber rim",(x,-.503,.698),.0138,.004,rubber,console,(0,-1,0))
            round_part(name+" cap",(x,-.506,.698),.0115,.003,mat,console,(0,-1,0))
        cuboid("Legend insert",(x,-.4971,.657),(.051,.001,.014),graphite,console,.001)
        font=bpy.data.curves.new(name+" face text",'FONT')
        font.body=name.upper() if name!="Emergency stop" else "E-STOP"
        font.align_x=font.align_y='CENTER'
        font.size=.008
        text=bpy.data.objects.new(name+" label",font)
        collection.objects.link(text)
        text.location=(x,-.498,.657)
        text.rotation_euler[0]=pi/2
        font.materials.append(legend)
        mesh=bpy.data.meshes.new_from_object(text.evaluated_get(bpy.context.evaluated_depsgraph_get()))
        letters=obj_with_mesh(name+" legend",mesh,tuple(text.location),console)
        letters.rotation_euler[0]=pi/2
        bpy.data.objects.remove(text,do_unlink=True)
    for x in (.367,.583):
        for z in (.648,.726):
            cap_screw("Faceplate screw",(x,-.4966,z),console,(0,-1,0),.0032)

    x,y=.689,.372
    round_part("Stack mounting flange",(x,y,.826),.035,.012,graphite,tower)
    for i in range(3):
        a=2*pi*i/3
        cap_screw("Tower flange captive screw",(x+.026*cos(a),y+.026*sin(a),.832),tower,radius=.0034)
    round_part("Stack aluminum stem",(x,y,.903),.012,.145,aluminum,tower)
    round_part("Signal tower hub",(x,y,.99),.026,.031,graphite,tower)
    for i,mat in enumerate((green,amber_off,red_off)):
        z=1.023+i*.038
        round_part("Signal lens",(x,y,z),.0237,.032,mat,tower,sides=48,edge=.0018)
        for d in (-.009,0,.009):
            round_part("Lens prismatic ring",(x,y,z+d),.02425,.00125,mat,tower,sides=48,edge=0)
        round_part("Tower separation collar",(x,y,z+.0185),.025,.005,graphite,tower)
    round_part("Stack end cap",(x,y,1.124),.025,.011,graphite,tower,edge=.002)
    return root
