"""MCP resources shipped with the cad-mcp server (SPEC 5.3).

Resources:
  - cad://session/current/code — full current model code
  - cad://examples/{name}      — curated example models
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from cad_mcp import session


def register(mcp: MCPServer) -> None:
    @mcp.resource(
        "cad://session/current/code",
        name="current_code",
        description="Full CadQuery source code of the current model.",
        mime_type="text/x-python",
    )
    def current_code() -> str:
        sess = session.get_or_create()
        code = sess.accumulated_code()
        if not code:
            return "# No model code yet. Call execute_cad to start."
        return code

    @mcp.resource(
        "cad://examples/{name}",
        name="example",
        description=(
            "Curated CadQuery example models. Available: bracket, "
            "enclosure, flange, pipe_clamp, phone_stand, "
            "threaded_cap, gear, desk_organizer"
        ),
        mime_type="text/x-python",
    )
    def example(name: str) -> str:
        if name not in EXAMPLES:
            available = ", ".join(sorted(EXAMPLES))
            return (
                f"# Unknown example '{name}'.\n"
                f"# Available examples: {available}\n"
            )
        return EXAMPLES[name]


# ── Curated example models ───────────────────────────────────────

EXAMPLES: dict[str, str] = {}

# 1. Wall-mount bracket (SPEC 9.1 target)
EXAMPLES["bracket"] = '''\
import cadquery as cq

# Wall-mount bracket for a 30mm pipe
# Two M4 screw holes, 3mm walls

pipe_od = 30
wall = 3
plate_w = pipe_od + 2 * wall + 20  # extra for screw tabs
plate_h = pipe_od / 2 + wall + 10
plate_t = wall

# Base plate with screw holes
base = (
    cq.Workplane("XY")
    .box(plate_w, plate_t, plate_h)
    .faces("<Y").workplane()
    .pushPoints([(plate_w / 2 - 8, 0), (-(plate_w / 2 - 8), 0)])
    .hole(4.5)  # M4 clearance
)

# Pipe cradle: half-cylinder on top of the plate
cradle = (
    cq.Workplane("XZ")
    .center(0, plate_h / 2)
    .circle(pipe_od / 2 + wall)
    .circle(pipe_od / 2)
    .extrude(plate_t)
    .translate((0, -plate_t / 2, 0))
)

# Cut away the top half of the outer cylinder to make an open cradle
cutter = (
    cq.Workplane("XY")
    .box(plate_w * 2, plate_t * 2, plate_h * 2)
    .translate((0, 0, plate_h / 2 + pipe_od / 2 + wall))
)

cradle = cradle.cut(cutter)
result = base.union(cradle)
'''

# 2. Electronics enclosure with lid
EXAMPLES["enclosure"] = '''\
import cadquery as cq

# Simple electronics enclosure: 80x50x30mm, 2mm walls, open top
inner_l, inner_w, inner_h = 76, 46, 28
wall = 2

# Outer shell
result = (
    cq.Workplane("XY")
    .box(inner_l + 2 * wall, inner_w + 2 * wall, inner_h + wall)
    .faces(">Z").shell(-wall)
    # Mounting posts in corners (for M3 screws)
    .faces("<Z").workplane(invert=True)
    .rect(inner_l - 6, inner_w - 6, forConstruction=True)
    .vertices()
    .circle(3).extrude(inner_h - 2)
    # Screw holes in posts
    .faces(">Z").workplane()
    .rect(inner_l - 6, inner_w - 6, forConstruction=True)
    .vertices()
    .hole(2.5)  # M3 tap
)
'''

# 3. Bolt-hole flange
EXAMPLES["flange"] = '''\
import cadquery as cq

# Flange: 70mm OD, 25mm bore, 6 bolt holes on 50mm PCD
od = 70
bore = 25
pcd = 50
bolt_d = 5.5  # M5 clearance
thickness = 8
n_bolts = 6

result = (
    cq.Workplane("XY")
    .circle(od / 2)
    .circle(bore / 2)
    .extrude(thickness)
    # Bolt holes
    .faces(">Z").workplane()
    .polarArray(pcd / 2, 0, 360, n_bolts)
    .hole(bolt_d)
    # Fillet outer edge
    .edges(">Z").fillet(1)
)
'''

# 4. Pipe clamp
EXAMPLES["pipe_clamp"] = '''\
import cadquery as cq

# Two-piece pipe clamp for 25mm OD pipe
pipe_od = 25
wall = 3
tab_w = 15
tab_h = 12
bolt_d = 4.5  # M4

r_outer = pipe_od / 2 + wall

# Bottom half
bottom = (
    cq.Workplane("XZ")
    .lineTo(r_outer, 0)
    .radiusArc((-r_outer, 0), -r_outer)
    .close()
    .extrude(tab_w)
    .translate((0, -tab_w / 2, 0))
)

# Add bolt tabs
tab_l = (
    cq.Workplane("XY")
    .box(2 * r_outer + 2 * tab_h, tab_w, wall)
    .translate((0, 0, -wall / 2))
)
bottom = bottom.union(tab_l)

# Bolt holes in tabs
result = (
    bottom
    .faces("<Z").workplane()
    .pushPoints([(r_outer + tab_h / 2, 0), (-(r_outer + tab_h / 2), 0)])
    .hole(bolt_d)
)
'''

# 5. Phone stand
EXAMPLES["phone_stand"] = '''\
import cadquery as cq
import math

# Angled phone stand: 70mm wide, 65-degree viewing angle
width = 70
depth = 50
thickness = 4
angle = 65  # degrees from horizontal
lip_h = 8   # front lip to hold the phone

# Back support plate (angled)
back = (
    cq.Workplane("XZ")
    .moveTo(0, 0)
    .lineTo(depth * math.cos(math.radians(angle)), 0)
    .lineTo(0, depth * math.sin(math.radians(angle)))
    .close()
    .extrude(width)
    .translate((0, -width / 2, 0))
)

# Front lip
lip = (
    cq.Workplane("XY")
    .box(thickness, width, lip_h)
    .translate((thickness / 2, 0, lip_h / 2))
)

# Base
base = (
    cq.Workplane("XY")
    .box(depth * math.cos(math.radians(angle)) + thickness, width, thickness)
    .translate((
        (depth * math.cos(math.radians(angle)) + thickness) / 2,
        0, -thickness / 2,
    ))
)

result = base.union(back).union(lip)
'''

# 6. Threaded cap (simplified thread representation)
EXAMPLES["threaded_cap"] = '''\
import cadquery as cq

# Threaded bottle cap (simplified external thread via helical cut)
cap_od = 30
cap_id = 26
cap_h = 15
grip_depth = 0.8
n_grips = 24

# Main cap body
cap = (
    cq.Workplane("XY")
    .circle(cap_od / 2)
    .circle(cap_id / 2)
    .extrude(cap_h)
)

# Add top
top = (
    cq.Workplane("XY")
    .workplane(offset=cap_h)
    .circle(cap_od / 2)
    .extrude(2)
)

# Knurling: small vertical cuts around the outside
knurl = (
    cq.Workplane("XY")
    .polarArray(cap_od / 2 + grip_depth / 2, 0, 360, n_grips)
    .rect(grip_depth, grip_depth)
    .extrude(cap_h)
)

result = cap.union(top).cut(knurl)
'''

# 7. Spur gear (simplified)
EXAMPLES["gear"] = '''\
import cadquery as cq
import math

# Simplified spur gear: 20 teeth, module 2, 8mm bore
n_teeth = 20
module = 2
bore = 8
thickness = 6

pitch_r = n_teeth * module / 2
outer_r = pitch_r + module
root_r = pitch_r - 1.25 * module
tooth_angle = 360 / n_teeth

# Build gear profile using polygon approximation
pts = []
for i in range(n_teeth):
    a0 = math.radians(i * tooth_angle)
    a1 = math.radians(i * tooth_angle + tooth_angle * 0.2)
    a2 = math.radians(i * tooth_angle + tooth_angle * 0.3)
    a3 = math.radians(i * tooth_angle + tooth_angle * 0.7)
    a4 = math.radians(i * tooth_angle + tooth_angle * 0.8)
    # Root -> outer -> outer -> root
    pts.append((root_r * math.cos(a0), root_r * math.sin(a0)))
    pts.append((root_r * math.cos(a1), root_r * math.sin(a1)))
    pts.append((outer_r * math.cos(a2), outer_r * math.sin(a2)))
    pts.append((outer_r * math.cos(a3), outer_r * math.sin(a3)))
    pts.append((root_r * math.cos(a4), root_r * math.sin(a4)))

result = (
    cq.Workplane("XY")
    .polyline(pts).close()
    .extrude(thickness)
    .faces(">Z").workplane()
    .hole(bore)
)
'''

# 8. Desk organizer
EXAMPLES["desk_organizer"] = '''\
import cadquery as cq

# Desk organizer: pen holder + card slot + tray
base_l, base_w, base_h = 120, 60, 5
wall = 2

# Base tray
base = (
    cq.Workplane("XY")
    .box(base_l, base_w, base_h)
)

# Pen holder section (right side): 40x40x60mm box, hollowed
pen_box = (
    cq.Workplane("XY")
    .box(40, base_w, 60)
    .faces(">Z").shell(-wall)
    .translate((base_l / 2 - 20, 0, 30 + base_h / 2))
)

# Card/phone slot (left side): thin tall slot
slot_w = 12
card_slot = (
    cq.Workplane("XY")
    .box(slot_w, base_w - 2 * wall, 50)
    .translate((-base_l / 2 + slot_w / 2 + wall, 0, 25 + base_h / 2))
)
card_inner = (
    cq.Workplane("XY")
    .box(slot_w - 2 * wall, base_w - 4 * wall, 50)
    .translate((-base_l / 2 + slot_w / 2 + wall, 0, 25 + base_h / 2 + wall))
)

result = base.union(pen_box).union(card_slot).cut(card_inner)
'''
