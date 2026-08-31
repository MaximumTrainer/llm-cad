"""MCP prompts shipped with the cad-mcp server (SPEC 5.2).

Three prompts teach the LLM how to use the server effectively:
  - design_workflow: the core loop with mandatory render-after-execute
  - cadquery_primer: CadQuery idioms cheat sheet with runnable examples
  - printability_checklist: FDM printing guidelines
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer


def register(mcp: MCPServer) -> None:
    @mcp.prompt(
        description=(
            "Step-by-step 3D design workflow: write CadQuery code, "
            "render, visually critique, measure, validate, export."
        ),
    )
    def design_workflow() -> str:
        return _DESIGN_WORKFLOW

    @mcp.prompt(
        description=(
            "CadQuery cheat sheet: Workplane basics, extrude/cut, "
            "fillets, shell, hole patterns, booleans — with runnable "
            "snippets."
        ),
    )
    def cadquery_primer() -> str:
        return _CADQUERY_PRIMER

    @mcp.prompt(
        description=(
            "FDM printability checklist: wall thickness, overhangs, "
            "bed adhesion, tolerances for mating parts."
        ),
    )
    def printability_checklist() -> str:
        return _PRINTABILITY_CHECKLIST


# ── design_workflow ──────────────────────────────────────────────

_DESIGN_WORKFLOW = """\
# 3D Design Workflow — cad-mcp

Follow these steps in order every time you design or modify a 3D model.
Do NOT skip any step.

## Step 1 — Write CadQuery code
Call `execute_cad` with Python/CadQuery code that assigns the final
shape to a variable named `result`.

- `cadquery` is pre-imported as `cq`; `math` and `numpy` are available.
- Use `mode="replace"` for a fresh model, `mode="append"` to add to
  the existing code.
- If execution fails, read the error (line number + hint), fix the
  code, and re-run before continuing.

## Step 2 — Render and visually critique (MANDATORY)
Call `render_views` **immediately after every successful execute_cad**.
Never skip this step.

When you receive the rendered image:
1. State what you see in each view (front, right, top, iso).
2. Compare what you see against the user's requirements:
   - Are all requested features visible?
   - Do proportions look correct?
   - Are there unexpected artifacts, gaps, or missing geometry?
3. If anything looks wrong, go back to Step 1 and revise.

Do NOT proceed to validation or export until the render matches the
user's intent.

## Step 3 — Verify dimensions with measure
Before declaring the model correct, call `measure` to confirm:
- `what="bbox"` — overall dimensions match requirements.
- `what="distance"` with selectors — critical dimensions (hole
  spacing, wall thickness, feature positions) are within 0.1mm of
  the target.
- `what="volume"` — sanity-check the volume.

If any measurement is off, return to Step 1 and adjust.

## Step 4 — Validate for printability
Call `validate_mesh` to check:
- Watertight and manifold mesh.
- Wall thickness above minimum (default 1.2mm).
- Overhang angles within limits.
- Review the PLA mass estimate.

If issues are found, return to Step 1 and fix. Common fixes:
- Non-watertight: ensure all booleans fully intersect.
- Thin walls: increase wall or shell thickness.
- Overhangs: add fillets, chamfers, or redesign angles.

## Step 5 — Export
Call `export_model` with the requested format(s):
- `step` for CAD interchange (exact B-rep, no tessellation loss).
- `stl` for 3D printing slicers.
- `3mf` for modern slicers with metadata.
- `glb` for web/AR viewers.

Report the file path and size to the user.

## Iteration rules
- You may loop Steps 1-3 as many times as needed.
- Target: a correct, validated model in 4 or fewer iterations.
- Each iteration: execute → render → critique → (measure if close) →
  fix or proceed.
"""


# ── cadquery_primer ──────────────────────────────────────────────

_CADQUERY_PRIMER = """\
# CadQuery Cheat Sheet for cad-mcp

All code runs in a sandbox. `cadquery` is available as `cq`, plus
`math` and `numpy`. Assign the final shape to `result`.

---

## 1. Workplane basics + extrude

A Workplane defines a 2D sketch plane. Draw on it, then extrude.

```python
import cadquery as cq

# A simple box: 60mm x 40mm x 5mm base plate
result = cq.Workplane("XY").box(60, 40, 5)
```

Extrude a custom 2D profile:

```python
import cadquery as cq

result = (
    cq.Workplane("XY")
    .rect(60, 40)       # 2D rectangle on XY
    .extrude(5)          # pull up 5mm in Z
)
```

---

## 2. Extrude and cut

Use `.cut()` or negative extrude to remove material.
`.hole(diameter)` is a shortcut for centered through-holes.

```python
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(60, 40, 10)
    # Select top face, start a new workplane on it
    .faces(">Z").workplane()
    # Cut a 20mm x 20mm pocket 5mm deep
    .rect(20, 20).cutBlind(-5)
)
```

Through-holes at specific positions:

```python
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(60, 40, 5)
    .faces(">Z").workplane()
    .pushPoints([(20, 10), (-20, 10), (20, -10), (-20, -10)])
    .hole(4.2)  # M4 clearance holes
)
```

---

## 3. Fillets — and how they fail

Fillets round edges. The radius MUST be smaller than the shortest
adjacent edge, or the kernel will error.

```python
import cadquery as cq

# Safe fillet: radius 2mm on a 10mm-tall box (shortest edge = 10mm)
result = (
    cq.Workplane("XY")
    .box(40, 30, 10)
    .edges("|Z").fillet(2)  # fillet all vertical edges
)
```

**Common failure**: fillet radius too large.

```python
# BAD — radius 8 on a 10mm edge will fail:
#   result = cq.Workplane("XY").box(40, 30, 10).edges("|Z").fillet(8)
# Fix: reduce radius to at most half the shortest edge.
```

**Fillet on specific edges only** (safer):

```python
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(40, 30, 10)
    .edges(">Z").fillet(1)   # only top edges
)
```

Chamfer as a safer alternative when fillet fails:

```python
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(40, 30, 10)
    .edges("|Z").chamfer(2)
)
```

---

## 4. Shell (hollow out)

`.shell(thickness)` hollows a solid, keeping walls of the given
thickness. Pass a negative value. Select faces to remove (open top).

```python
import cadquery as cq

# Box with open top, 2mm walls
result = (
    cq.Workplane("XY")
    .box(40, 30, 20)
    .faces(">Z").shell(-2)  # remove top face, 2mm walls
)
```

Shell before adding small features (holes, fillets) — shell on
complex geometry can fail.

---

## 5. Hole patterns with polarArray

`polarArray` arranges features in a circle. Parameters:
`(radius, startAngle, totalAngle, count)`.

```python
import cadquery as cq

# Flange with 6 bolt holes on a 25mm-radius circle
result = (
    cq.Workplane("XY")
    .circle(35).extrude(5)         # outer disc
    .faces(">Z").workplane()
    .polarArray(25, 0, 360, 6)     # 6 points at R=25
    .hole(5.5)                      # M5 clearance
    .faces(">Z").workplane()
    .hole(12)                       # center bore
)
```

Rectangular patterns use `.rarray(xSpacing, ySpacing, xCount, yCount)`:

```python
import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(80, 60, 5)
    .faces(">Z").workplane()
    .rarray(20, 20, 3, 2)   # 3x2 grid, 20mm spacing
    .hole(4.2)
)
```

---

## 6. Booleans: union, cut, intersect

Combine solids with `.union()`, `.cut()`, `.intersect()`.

```python
import cadquery as cq

base = cq.Workplane("XY").box(50, 50, 5)
post = cq.Workplane("XY").cylinder(20, 8)  # height=20, radius=8

result = base.union(post)
```

L-bracket via union of two perpendicular plates:

```python
import cadquery as cq

base_plate = cq.Workplane("XY").box(50, 30, 3)
back_wall = (
    cq.Workplane("XZ")
    .center(0, 1.5)       # shift up half of base thickness
    .rect(50, 30)
    .extrude(3)
    .translate((0, -15, 15))
)

result = base_plate.union(back_wall)
```

---

## 7. Selectors reference

CadQuery selectors pick faces, edges, and vertices for operations.

| Selector | Meaning |
|----------|---------|
| `">Z"` | Topmost face (max Z normal) |
| `"<Z"` | Bottommost face |
| `">Y"` | Front face (max Y) |
| `"<Y"` | Back face |
| `"\\|Z"` | Edges parallel to Z axis |
| `">Z"` on edges | Topmost edges |
| `"#Z"` | Faces with normal perpendicular to Z |

Chain selectors on a workplane to position features precisely:
```python
.faces(">Z").workplane()           # sketch on top face
.faces("<Y").workplane()           # sketch on back face
.transformed(offset=(0, 0, 10))   # shift workplane origin
```

---

## Tips
- Always verify with `render_views` after `execute_cad`.
- Use `measure(what="bbox")` to confirm dimensions.
- Keep fillet radii conservative (≤ 1/3 shortest edge).
- Shell before adding small features.
- If an operation fails, simplify: fewer fillets, simpler geometry.
"""


# ── printability_checklist ───────────────────────────────────────

_PRINTABILITY_CHECKLIST = """\
# FDM Printability Checklist

Use `validate_mesh` to check these automatically. This list explains
what the checks mean and how to fix failures.

## 1. Wall thickness
- **Minimum**: 1.2mm for structural parts (2 perimeters at 0.4mm nozzle).
- **Functional minimum**: 0.8mm (single wall) — fragile, use only for
  non-structural cosmetic features.
- **Fix**: increase shell thickness or add ribs/gussets.
- **Check**: `validate_mesh(min_wall_mm=1.2)` — look at
  `wall_thickness.violations`.

## 2. Overhangs
- **Threshold**: 45° from vertical is the standard FDM limit without
  support.
- Angles steeper than 45° (more horizontal) will sag or need support
  material.
- **Fix**: add chamfers or fillets to transition zones, redesign
  angles, or accept support material.
- **Check**: `validate_mesh(max_overhang_deg=45)` — look at
  `overhangs.overhang_faces`.

## 3. Bed adhesion
- First layer should have enough contact area with the build plate.
- Avoid small point contacts — add a brim in the slicer or design a
  wider base.
- Tall, narrow parts are prone to tipping — consider orientation.
- Rule of thumb: base footprint should be ≥ 30% of the bounding box
  XY footprint.

## 4. Bridging
- Horizontal spans up to ~10mm bridge well on most printers.
- Longer bridges sag — add intermediate supports or split the span.
- Circular holes ≤ 10mm diameter bridge without support if oriented
  upward.

## 5. Tolerances for mating parts
- **Press fit**: design 0.1mm interference (hole 0.1mm smaller than
  pin).
- **Sliding fit**: add 0.2-0.3mm clearance per side.
- **Screw holes**: use nominal clearance diameters:
  - M3: 3.4mm hole
  - M4: 4.5mm hole
  - M5: 5.5mm hole
- **Threaded inserts**: size hole per insert datasheet (typically
  insert OD - 0.2mm).
- Print a test fit block before committing to a full model.

## 6. Mesh quality
- **Watertight**: all faces must form a closed volume — no gaps, no
  flipped normals.
- **Manifold**: every edge shared by exactly 2 faces — no T-junctions,
  no self-intersections.
- **Fix**: ensure boolean operations fully overlap; avoid tangent-only
  contacts.
- **Check**: `validate_mesh()` — look at `watertight` and `manifold`.

## 7. Mass estimate
- `validate_mesh` reports estimated PLA mass (density 1.24 g/cm³).
- Typical FDM infill is 15-20%, so actual mass is roughly 30-40% of
  the solid estimate (walls + infill + top/bottom).
- Use the solid mass to estimate material cost:
  PLA is about $20/kg, so cost = mass_g x 0.02 x infill_fraction.
"""
