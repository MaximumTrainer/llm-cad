"""Headless scenery generation for one rownative course (SPEC 10.5, issue #42).

``llm-cad generate-course`` turns a rownative course JSON into a few
scene-ready GLBs plus the placements that put them on the route. It is
what virtualrow's monthly scenery workflow shells out to, so it runs with
no MCP host in the loop:

1. summarise the course for the model (name, place, length, notes,
   polygon names) -- the course file itself is supplied by the caller,
   so nothing here touches the network except the model call;
2. ask the model, once, for a JSON plan: up to ``max_models`` objects,
   each with CadQuery code and a placement;
3. run each model's code through :mod:`cad_mcp.sandbox` -- the same
   sandbox ``execute_cad`` uses, never ``exec()`` -- giving a failed
   model one repair round with the structured error;
4. colour every part (the model's hex, else a palette default from the
   part's name or the model's role -- see :mod:`cad_mcp.scenery_palette`)
   and tessellate to GLB at a tolerance scaled to the model's size (scenery
   is metres across, and the 0.1 mm export tolerance would produce
   millions of triangles), coarsening once if the gzipped size is over
   the 500 KB cap;
5. write ``<slug>.glb``, ``placements.json`` and ``report.json``.

Determinism: given the same model replies, every output byte is a
function of the inputs. Nothing records a time, a temp path or a random
id; floats are rounded; JSON is written with sorted, fixed formatting;
tessellation and GLB export are already byte-stable (SPEC N4).
"""
from __future__ import annotations

import gzip
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cad_mcp import sandbox, scenery_palette
from cad_mcp.llm_client import Message, ModelClient, ModelError
from cad_mcp.paths import safe_output_path

#: virtualrow NFR2: each GLB must be at most this many bytes gzipped.
GZIP_CAP_BYTES = 500 * 1024

BANK_SIDES = ("left", "right", "centre")

# Tessellation for scenery. Linear deflection is a fraction of the
# model's bounding-box diagonal, floored so a tiny model is not
# over-refined; the second step is the coarser retry used when the first
# GLB is over the size cap.
_TESS_STEPS = (0.002, 0.008)
_TESS_MIN_LINEAR_MM = 1.0
_TESS_ANGULAR_RAD = 0.5

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")
_FENCE_RE = re.compile(r"```(?:json|python)?\s*(.*?)```", re.DOTALL)

SYSTEM_PROMPT = """\
You design scenery for a virtual rowing simulator using CadQuery.

Each model is made of one or more coloured parts. Each part's code is
Python that runs in its own sandbox with `cadquery` (also `cq`), `math`
and `numpy` pre-imported, and MUST assign that part's shape to a variable
named `result`. No imports beyond those, no file or network access. All
parts of a model share one coordinate frame, so position each part where
it belongs in the model.

Conventions every model must follow:
- Units are millimetres (a 12 m boathouse is 12000 mm wide).
- Centred on X/Y; Z = 0 is where the model meets the water or bank.
- +Y faces the water.
- Keep geometry simple and blocky: a few dozen boxes, cylinders and
  extrusions. No fillets, no text, no lofts over many sections. The
  exported mesh must stay small.
- Largest dimension at most 60 000 mm.
- Colour is flat, one #RRGGBB per part, no textures: split a model into
  parts wherever the real thing changes material (walls, roof, windows,
  doors; deck, railings; trunk, foliage). Prefer this palette so models
  match the existing scenery: {palette}.

Reply with one JSON object and nothing else, in this shape:
{"models": [{
  "slug": "short-kebab-name",
  "role": "hero-landmark" | "bank-material" | "backdrop",
  "description": "one sentence",
  "parts": [{
    "name": "roof",
    "colour": "#4A3F35",
    "code": "CadQuery source assigning result"
  }],
  "placement": {
    "distanceAlongRouteMeters": number,
    "bankSide": "left" | "right" | "centre",
    "lateralOffsetMeters": number,
    "orientationDegrees": number,
    "scale": number
  }
}]}
""".replace("{palette}", scenery_palette.palette_prompt())


class CourseError(ValueError):
    """The course JSON is unusable; nothing should be generated."""


@dataclass(frozen=True)
class Course:
    id: str
    name: str
    country: str
    distance_m: float | None
    center: tuple[float, float] | None
    notes: str
    polygon_names: tuple[str, ...]


@dataclass
class PartPlan:
    name: str
    colour: str
    colour_source: str
    code: str


@dataclass
class ModelPlan:
    slug: str
    role: str
    description: str
    parts: list[PartPlan]
    placement: dict[str, Any]


@dataclass
class Outcome:
    """What happened to one planned model, for ``report.json``."""

    slug: str
    role: str
    model_id: str
    filename: str | None = None
    glb_bytes: int | None = None
    glb_gzip_bytes: int | None = None
    repaired: bool = False
    error: str | None = None
    placement: dict[str, Any] | None = None
    parts: list[dict[str, str]] = field(default_factory=list)


@dataclass
class GenerationResult:
    course_id: str
    model: str
    seed: int
    max_models: int
    llm_cad_sha: str
    outcomes: list[Outcome] = field(default_factory=list)
    plan_error: str | None = None

    @property
    def written(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.filename is not None]


# ----------------------------------------------------------------------
# Course input
# ----------------------------------------------------------------------


def load_course(path: Path) -> Course:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise CourseError(f"course file not found: {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CourseError(f"course file is not readable JSON: {exc}") from None
    if not isinstance(raw, dict):
        raise CourseError("course JSON must be an object")

    course_id = _ID_RE.sub("-", str(raw.get("id", "")).strip()).strip("-")
    if not course_id:
        raise CourseError("course JSON has no usable 'id'")

    distance = raw.get("distance_m", raw.get("distanceMeters"))
    distance_m = (
        float(distance)
        if isinstance(distance, int | float) and distance > 0
        else None
    )
    lat = raw.get("center_lat", raw.get("lat"))
    lon = raw.get("center_lon", raw.get("lng"))
    center = (
        (float(lat), float(lon))
        if isinstance(lat, int | float) and isinstance(lon, int | float)
        else None
    )
    polygons = raw.get("polygons") or []
    names = tuple(
        str(p.get("name", "")).strip()
        for p in sorted(
            (p for p in polygons if isinstance(p, dict)),
            key=lambda p: p.get("order", 0) if isinstance(p.get("order"), int) else 0,
        )
        if str(p.get("name", "")).strip()
    )
    return Course(
        id=course_id,
        name=str(raw.get("name") or course_id).strip(),
        country=str(raw.get("country") or "").strip(),
        distance_m=distance_m,
        center=center,
        notes=str(raw.get("notes") or "").strip()[:1500],
        polygon_names=names,
    )


def course_prompt(course: Course, max_models: int) -> str:
    lines = [f"Course: {course.name}"]
    if course.country:
        lines.append(f"Country: {course.country}")
    if course.center:
        lines.append(f"Centre: {course.center[0]:.5f}, {course.center[1]:.5f}")
    if course.distance_m:
        lines.append(f"Route length: {course.distance_m:.0f} m")
    if course.polygon_names:
        lines.append("Course gates in order: " + ", ".join(course.polygon_names))
    if course.notes:
        lines.append(f"Notes: {course.notes}")
    upper = (
        f"0 to {course.distance_m:.0f}" if course.distance_m else "0 or more"
    )
    lines += [
        "",
        f"Design at most {max_models} model(s) that would make a rower "
        "recognise this course. In order of priority: one hero landmark "
        "(a real, distinctive building or bridge on this water if you "
        "know one), one distinctive bank material or edge treatment, and "
        "one backdrop element.",
        f"distanceAlongRouteMeters must be {upper}.",
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# Model replies
# ----------------------------------------------------------------------


def _json_from_reply(reply: str) -> Any:
    text = reply.strip()
    fenced = _FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise ModelError("model reply was not a JSON object")


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")[:48].strip("-")
    return slug or "model"


def parse_plan(reply: str, max_models: int) -> list[ModelPlan]:
    data = _json_from_reply(reply)
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list) or not models:
        raise ModelError("model reply had no 'models' list")

    plans: list[ModelPlan] = []
    seen: set[str] = set()
    for entry in models[:max_models]:
        if not isinstance(entry, dict):
            continue
        base = slugify(str(entry.get("slug") or entry.get("role") or "model"))
        slug, n = base, 2
        while slug in seen:
            slug = f"{base}-{n}"
            n += 1
        seen.add(slug)
        placement = entry.get("placement")
        role = str(entry.get("role") or "").strip()
        plans.append(
            ModelPlan(
                slug=slug,
                role=role,
                description=str(entry.get("description") or "").strip(),
                parts=_parse_parts(entry, slug, role),
                placement=placement if isinstance(placement, dict) else {},
            )
        )
    if not plans:
        raise ModelError("model reply had no usable model entries")
    return plans


def _parse_parts(entry: dict[str, Any], slug: str, role: str) -> list[PartPlan]:
    """The model's parts, each with a resolved colour.

    A model given as a single ``code`` (no ``parts``) is one part named
    after the model, coloured from a top-level ``colour`` if present.
    """
    raw_parts = entry.get("parts")
    if not isinstance(raw_parts, list) or not raw_parts:
        raw_parts = [
            {
                "name": slug,
                "colour": entry.get("colour", entry.get("color")),
                "code": entry.get("code"),
            }
        ]
    parts: list[PartPlan] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_parts):
        if not isinstance(raw, dict):
            continue
        base = slugify(str(raw.get("name") or f"part-{i + 1}"))
        name, n = base, 2
        while name in seen:
            name = f"{base}-{n}"
            n += 1
        seen.add(name)
        colour, source = scenery_palette.resolve_colour(
            raw.get("colour", raw.get("color")), name, role
        )
        parts.append(
            PartPlan(
                name=name,
                colour=colour,
                colour_source=source,
                code=str(raw.get("code") or ""),
            )
        )
    return parts


def normalise_placement(
    raw: dict[str, Any], distance_m: float | None
) -> dict[str, Any]:
    """Coerce a model's placement into virtualrow's schema, or raise.

    Distance is clamped onto the route (a landmark 10 m past the finish
    is still useful); anything that cannot be coerced is rejected, so a
    bad placement fails that model loudly instead of shipping garbage.
    """

    def number(key: str, default: float | None = None) -> float:
        value = raw.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"placement.{key} must be a number, got {value!r}")
        if not math.isfinite(value):
            raise ValueError(f"placement.{key} must be finite")
        return float(value)

    distance = max(0.0, number("distanceAlongRouteMeters"))
    if distance_m is not None:
        distance = min(distance, distance_m)
    side = raw.get("bankSide")
    if side == "center":
        side = "centre"
    if side not in BANK_SIDES:
        raise ValueError(
            f"placement.bankSide must be one of {BANK_SIDES}, got {side!r}"
        )
    orientation = number("orientationDegrees", 0.0)
    if not -360.0 <= orientation <= 360.0:
        orientation = math.fmod(orientation, 360.0)
    scale = number("scale", 1.0)
    if scale <= 0:
        raise ValueError(f"placement.scale must be > 0, got {scale}")
    return {
        "distanceAlongRouteMeters": _r(distance),
        "bankSide": side,
        "lateralOffsetMeters": _r(number("lateralOffsetMeters", 0.0)),
        "orientationDegrees": _r(orientation),
        "scale": _r(scale),
    }


def _r(value: float) -> float:
    # Round for stable JSON; normalise -0.0 so it prints as 0.0.
    return round(value, 3) + 0.0


# ----------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------


_Part = tuple[str, Path, str]  # (name, brep, #RRGGBB)


def _union_diagonal(bboxes: list[dict[str, float] | None]) -> float:
    boxes = [b for b in bboxes if b]
    if not boxes:
        return 1000.0
    lo = [min(b[f"{a}min"] for b in boxes) for a in "xyz"]
    hi = [max(b[f"{a}max"] for b in boxes) for a in "xyz"]
    return math.dist(lo, hi)


def _write_coloured_glb(
    parts: list[_Part], out: Path, linear: float
) -> None:
    """One mesh per part, each with a flat PBR ``baseColorFactor``.

    The same shape as virtualrow's kit GLBs: no textures, one material
    per part, fully rough and non-metallic so the scene's lighting does
    the shading.
    """
    import trimesh
    from trimesh.visual.material import PBRMaterial
    from trimesh.visual.texture import TextureVisuals

    from cad_mcp.render import load_and_tessellate

    scene = trimesh.Scene()
    for name, brep, colour in parts:
        verts, faces = load_and_tessellate(brep, linear, _TESS_ANGULAR_RAD)
        mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
        mesh.fix_normals()
        mesh.visual = TextureVisuals(
            material=PBRMaterial(
                name=name,
                baseColorFactor=scenery_palette.rgba(colour),
                metallicFactor=0.0,
                roughnessFactor=1.0,
            )
        )
        scene.add_geometry(mesh, geom_name=name, node_name=name)
    out.write_bytes(scene.export(file_type="glb"))


def _glb_for(
    parts: list[_Part], bboxes: list[dict[str, float] | None], out: Path
) -> tuple[int, int]:
    """Export *parts* to one GLB at *out*; return (raw bytes, gzipped bytes).

    Tessellation is relative to the whole model's size. Tries each step
    in turn and keeps the first GLB under the cap; raises ``ValueError``
    when even the coarsest is too big.
    """
    diagonal = _union_diagonal(bboxes)
    gz = 0
    for fraction in _TESS_STEPS:
        linear = max(diagonal * fraction, _TESS_MIN_LINEAR_MM)
        _write_coloured_glb(parts, out, linear)
        data = out.read_bytes()
        gz = len(gzip.compress(data, compresslevel=9, mtime=0))
        if gz <= GZIP_CAP_BYTES:
            return len(data), gz
    out.unlink(missing_ok=True)
    raise ValueError(
        f"GLB is {gz} bytes gzipped even at the coarsest tessellation; "
        f"the cap is {GZIP_CAP_BYTES}"
    )


def _run_code(code: str, work: Path, brep: Path) -> sandbox.SandboxResult:
    if not code.strip():
        return sandbox.SandboxResult(
            ok=False, error_type="EmptyCode", message="the model supplied no code"
        )
    return sandbox.run(code, work, brep_out=brep)


def _repair_messages(
    history: list[Message], plan: ModelPlan, part: PartPlan, failure: str
) -> list[Message]:
    return [
        *history,
        {
            "role": "user",
            "content": (
                f"The code for part '{part.name}' of '{plan.slug}' failed in "
                f"the sandbox:\n{failure}\n\n"
                'Reply with one JSON object {"code": "..."} holding the '
                "corrected CadQuery source, and nothing else."
            ),
        },
    ]


# ----------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------


def _build_part(
    client: ModelClient,
    history: list[Message],
    plan: ModelPlan,
    part: PartPlan,
    work: Path,
    brep: Path,
    model: str,
    seed: int,
) -> tuple[sandbox.SandboxResult, bool] | str:
    """Run one part, with one repair round. Returns (result, repaired)
    on success, or the error text for ``report.json``."""
    run = _run_code(part.code, work, brep)
    if run.ok:
        return run, False
    try:
        fix = client.complete(
            _repair_messages(history, plan, part, run.format_for_llm()),
            model=model,
            seed=seed,
        )
        fixed = _json_from_reply(fix)
    except ModelError as exc:
        return f"RepairFailed ({part.name}): {exc}"
    code = fixed.get("code") if isinstance(fixed, dict) else None
    run = _run_code(str(code or ""), work, brep)
    if not run.ok:
        return f"{part.name}: {run.format_for_llm()}"
    return run, True


def llm_cad_sha() -> str:
    """The llm-cad commit this run came from, for ``report.json``."""
    pinned = os.environ.get("LLM_CAD_GIT_SHA", "").strip()
    if pinned:
        return pinned
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    sha = out.stdout.strip()
    return sha if out.returncode == 0 and sha else "unknown"


def generate(
    course: Course,
    out_dir: Path,
    *,
    client: ModelClient,
    model: str,
    seed: int,
    max_models: int,
) -> GenerationResult:
    """Run the whole pipeline and write the outputs into *out_dir*."""
    result = GenerationResult(
        course_id=course.id,
        model=model,
        seed=seed,
        max_models=max_models,
        llm_cad_sha=llm_cad_sha(),
    )
    history: list[Message] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": course_prompt(course, max_models)},
    ]
    try:
        reply = client.complete(history, model=model, seed=seed)
        plans = parse_plan(reply, max_models)
    except ModelError as exc:
        result.plan_error = str(exc)
        _write_outputs(out_dir, result, staged={})
        return result
    history.append({"role": "assistant", "content": reply})

    staged: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="llm-cad-course-") as tmp:
        work = Path(tmp)
        for plan in plans:
            outcome = Outcome(
                slug=plan.slug,
                role=plan.role,
                model_id=f"r-{course.id}-{plan.slug}"[:198],
            )
            result.outcomes.append(outcome)
            try:
                outcome.placement = normalise_placement(
                    plan.placement, course.distance_m
                )
            except ValueError as exc:
                outcome.error = f"InvalidPlacement: {exc}"
                continue

            outcome.parts = [
                {"name": p.name, "colour": p.colour, "colourSource": p.colour_source}
                for p in plan.parts
            ]
            if not plan.parts:
                outcome.error = "NoParts: the model entry had no usable parts"
                continue
            built: list[_Part] = []
            bboxes: list[dict[str, float] | None] = []
            for i, part in enumerate(plan.parts):
                brep = work / f"{plan.slug}--{i}.brep"
                run = _build_part(client, history, plan, part, work, brep, model, seed)
                if isinstance(run, str):
                    outcome.error = run
                    break
                outcome.repaired |= run[1]
                built.append((part.name, brep, part.colour))
                bboxes.append(run[0].bbox)
            if outcome.error:
                continue

            filename = f"{plan.slug}.glb"
            glb = work / filename
            try:
                raw, gz = _glb_for(built, bboxes, glb)
            except ValueError as exc:
                outcome.error = f"SizeCapExceeded: {exc}"
                continue
            except Exception as exc:  # a kernel failure in tessellation
                outcome.error = f"ExportFailed: {type(exc).__name__}: {exc}"
                continue
            outcome.glb_bytes, outcome.glb_gzip_bytes = raw, gz
            outcome.filename = filename
            staged[filename] = glb

        _write_outputs(out_dir, result, staged)
    return result


def _write_outputs(
    out_dir: Path, result: GenerationResult, staged: dict[str, Path]
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for filename, src in staged.items():
        shutil.copyfile(src, safe_output_path(out_dir, filename))

    if result.written:
        placements = [
            {
                "modelId": o.model_id,
                "filename": o.filename,
                **(o.placement or {}),
            }
            for o in result.written
        ]
        _write_json(safe_output_path(out_dir, "placements.json"), placements)

    report = {
        "courseId": result.course_id,
        "model": result.model,
        "seed": result.seed,
        "maxModels": result.max_models,
        "llmCadSha": result.llm_cad_sha,
        "gzipCapBytes": GZIP_CAP_BYTES,
        "planError": result.plan_error,
        "models": [
            {
                "modelId": o.model_id,
                "slug": o.slug,
                "role": o.role,
                "filename": o.filename,
                "glbBytes": o.glb_bytes,
                "glbGzipBytes": o.glb_gzip_bytes,
                "repaired": o.repaired,
                "parts": o.parts,
                "error": o.error,
            }
            for o in result.outcomes
        ],
        "failures": [
            {"modelId": o.model_id, "error": o.error}
            for o in result.outcomes
            if o.error is not None
        ],
    }
    _write_json(safe_output_path(out_dir, "report.json"), report)


def _write_json(path: Path, data: Any) -> None:
    path.write_text(
        json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
