"""``llm-cad generate-course`` (SPEC 10.5, issue #42).

The model is always stubbed here: the point is that everything *after*
the model reply -- sandboxing, tessellation, the size cap, placements,
the report and their bytes -- is correct and reproducible. Live model
quality is out of scope for #42.
"""
from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from cad_mcp import cli, course_gen, scenery_palette
from cad_mcp.llm_client import (
    API_KEY_ENV,
    Message,
    ModelError,
    OpenAICompatibleClient,
)

FIXTURE = Path(__file__).parent / "fixtures" / "rownative-course-1.json"

BOATHOUSE_WALLS = (
    'result = cq.Workplane("XY").box(12000, 8000, 6000).translate((0, 0, 3000))\n'
)
BOATHOUSE_ROOF = """\
result = (
    cq.Workplane("YZ").polyline([(-4200, 6000), (4200, 6000), (0, 8500)])
    .close().extrude(12400).translate((-6200, 0, 0))
)
"""
BOATHOUSE_DOORS = (
    'result = cq.Workplane("XY").box(8000, 100, 3000).translate((0, 4050, 1500))\n'
)

REEDS = """\
result = cq.Workplane("XY")
for i in range(8):
    result = result.union(
        cq.Workplane("XY").cylinder(1500, 60).translate((i * 400 - 1400, 0, 750))
    )
"""

# Deliberately broken: the repair round must fix it.
BROKEN_HILLS = 'result = cq.Workplane("XY").box(30000, 5000)\n'
FIXED_HILLS = (
    'result = cq.Workplane("XY").box(30000, 5000, 4000).translate((0, 0, 2000))\n'
)

PLAN = {
    "models": [
        {
            "slug": "Lake Quinsig Boathouse",
            "role": "hero-landmark",
            "description": "Two-storey clapboard boathouse.",
            "parts": [
                # A colour from the model, in lower case and without '#'.
                {"name": "walls", "colour": "c9bfa8", "code": BOATHOUSE_WALLS},
                {"name": "roof", "colour": "#4A3F35", "code": BOATHOUSE_ROOF},
                # No colour: the part name picks the palette entry.
                {"name": "boat bay doors", "code": BOATHOUSE_DOORS},
            ],
            "placement": {
                "distanceAlongRouteMeters": 1500,
                "bankSide": "right",
                "lateralOffsetMeters": 4.5,
                "orientationDegrees": 0,
                "scale": 1,
            },
        },
        {
            "slug": "reed-bank",
            "role": "bank-material",
            "description": "A clump of reeds.",
            "colour": "not-a-colour",  # invalid: falls back to the name
            "code": REEDS,
            "placement": {
                "distanceAlongRouteMeters": 99999,  # clamped to the route
                "bankSide": "left",
                "lateralOffsetMeters": 1.25,
                "orientationDegrees": -90,
                "scale": 1.5,
            },
        },
        {
            "slug": "hills",
            "role": "backdrop",
            "description": "Low wooded hills.",
            "code": BROKEN_HILLS,
            "placement": {
                "distanceAlongRouteMeters": 2600,
                "bankSide": "center",  # US spelling is accepted
                "lateralOffsetMeters": 0,
                "orientationDegrees": 0,
                "scale": 2,
            },
        },
    ]
}


class StubClient:
    """Answers the plan request, then any repair request, from fixtures."""

    def __init__(self, plan: dict[str, Any] | str = PLAN) -> None:
        self.plan = plan
        self.calls: list[tuple[list[Message], str, int]] = []

    def complete(self, messages: list[Message], *, model: str, seed: int) -> str:
        self.calls.append((messages, model, seed))
        if "failed in the sandbox" in messages[-1]["content"]:
            return "```json\n" + json.dumps({"code": FIXED_HILLS}) + "\n```"
        return self.plan if isinstance(self.plan, str) else json.dumps(self.plan)


def _run(out: Path, client: StubClient | None = None, *extra: str) -> int:
    argv = [
        "generate-course",
        "--course-json", str(FIXTURE),
        "--out", str(out),
        "--model", "stub/model-1",
        "--seed", "7",
        *extra,
    ]
    return cli.main(argv, client=client or StubClient())


# ----------------------------------------------------------------------
# Fast tier: no sandbox, no tessellation
# ----------------------------------------------------------------------


def test_help_lists_every_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["generate-course", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--course-json", "--out", "--model", "--seed", "--max-models"):
        assert flag in out


def test_console_script_is_declared() -> None:
    pyproject = (Path(__file__).parents[1] / "pyproject.toml").read_text()
    assert 'llm-cad = "cad_mcp.cli:entrypoint"' in pyproject


def test_without_api_key_exits_nonzero_and_writes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    out = tmp_path / "out"
    code = cli.main(
        ["generate-course", "--course-json", str(FIXTURE), "--out", str(out)]
    )
    assert code == cli.EXIT_USAGE
    assert not out.exists()
    assert API_KEY_ENV in capsys.readouterr().err


def test_unreadable_course_exits_nonzero_and_writes_nothing(tmp_path: Path) -> None:
    bad = tmp_path / "course.json"
    bad.write_text("not json")
    out = tmp_path / "out"
    code = cli.main(
        ["generate-course", "--course-json", str(bad), "--out", str(out)],
        client=StubClient(),
    )
    assert code == cli.EXIT_USAGE
    assert not out.exists()


def test_unusable_model_reply_exits_1_with_a_report(tmp_path: Path) -> None:
    out = tmp_path / "out"
    assert _run(out, StubClient(plan="I cannot help with that.")) == 1
    assert sorted(p.name for p in out.iterdir()) == ["report.json"]
    report = json.loads((out / "report.json").read_text())
    assert report["planError"]
    assert report["models"] == []


def test_load_course_reads_the_rownative_shape() -> None:
    course = course_gen.load_course(FIXTURE)
    assert course.id == "1"
    assert course.distance_m == 5306
    assert course.polygon_names[0] == "Start"
    assert "Quinsig" in course_gen.course_prompt(course, 3)


def test_parse_plan_handles_fences_duplicates_and_the_cap() -> None:
    reply = "Here you go:\n```json\n" + json.dumps(
        {"models": [{"slug": "Tower!"}, {"slug": "tower"}, {"slug": "x"}]}
    ) + "\n```"
    plans = course_gen.parse_plan(reply, max_models=2)
    assert [p.slug for p in plans] == ["tower", "tower-2"]
    with pytest.raises(ModelError):
        course_gen.parse_plan('{"models": []}', 3)


def test_normalise_placement_clamps_and_rejects() -> None:
    norm = course_gen.normalise_placement(
        {"distanceAlongRouteMeters": -5, "bankSide": "center", "scale": 2}, 100.0
    )
    assert norm == {
        "distanceAlongRouteMeters": 0.0,
        "bankSide": "centre",
        "lateralOffsetMeters": 0.0,
        "orientationDegrees": 0.0,
        "scale": 2.0,
    }
    assert course_gen.normalise_placement(
        {
            "distanceAlongRouteMeters": 500,
            "bankSide": "left",
            "orientationDegrees": 450,
        },
        100.0,
    )["distanceAlongRouteMeters"] == 100.0
    for bad in (
        {"distanceAlongRouteMeters": 1, "bankSide": "middle"},
        {"distanceAlongRouteMeters": 1, "bankSide": "left", "scale": 0},
        {"distanceAlongRouteMeters": "far", "bankSide": "left"},
    ):
        with pytest.raises(ValueError):
            course_gen.normalise_placement(bad, None)


def test_every_part_gets_a_colour() -> None:
    resolve = scenery_palette.resolve_colour
    assert resolve("#4a3f35", "roof", "backdrop") == ("#4A3F35", "model")
    assert resolve(None, "front-windows", "") == ("#2A3A44", "part-name")
    assert resolve("red", "upper-roof", "") == ("#4A3F35", "part-name")
    assert resolve(None, "thing", "bank-material") == ("#5E8A46", "role")
    assert resolve(None, "thing", "Hero-Landmark") == ("#C9BFA8", "role")
    assert resolve(None, "thing", "mystery") == (
        scenery_palette.FALLBACK_COLOUR,
        "fallback",
    )
    assert scenery_palette.rgba("#FF0000") == [1.0, 0.0, 0.0, 1.0]


def test_single_code_model_becomes_one_coloured_part() -> None:
    plans = course_gen.parse_plan(json.dumps(PLAN), 3)
    boathouse, reeds, hills = plans
    assert [(p.name, p.colour, p.colour_source) for p in boathouse.parts] == [
        ("walls", "#C9BFA8", "model"),
        ("roof", "#4A3F35", "model"),
        ("boat-bay-doors", "#241E18", "part-name"),
    ]
    assert [(p.name, p.colour_source) for p in reeds.parts] == [
        ("reed-bank", "part-name")
    ]
    assert reeds.parts[0].colour == "#A89A6E"
    assert hills.parts[0].colour == "#3E6B32"


def test_system_prompt_offers_the_palette() -> None:
    assert "#4A3F35" in course_gen.SYSTEM_PROMPT
    assert "{palette}" not in course_gen.SYSTEM_PROMPT


def test_http_client_sends_seed_and_never_leaks_the_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "hi"}}]}
            )
        return httpx.Response(401, json={"error": "bad key"})

    client = OpenAICompatibleClient(
        "sk-secret-123",
        base_url="https://llm.example/v1",
        transport=httpx.MockTransport(handler),
    )
    msgs: list[Message] = [{"role": "user", "content": "x"}]
    assert client.complete(msgs, model="m", seed=7) == "hi"
    body = json.loads(seen[0].content)
    assert body["seed"] == 7 and body["temperature"] == 0 and body["model"] == "m"
    assert seen[0].url == "https://llm.example/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer sk-secret-123"

    with pytest.raises(ModelError) as exc:
        client.complete(msgs, model="m", seed=7)
    assert "401" in str(exc.value)
    assert "sk-secret-123" not in str(exc.value)


# ----------------------------------------------------------------------
# Full pipeline: real sandbox and tessellation, stubbed model
# ----------------------------------------------------------------------

_MODEL_ID_RE = re.compile(r"^r-[A-Za-z0-9_-]{1,196}$")
_PLACEMENT_KEYS = {
    "modelId",
    "filename",
    "distanceAlongRouteMeters",
    "bankSide",
    "lateralOffsetMeters",
    "orientationDegrees",
    "scale",
}


def _glb_colours(path: Path) -> dict[str, str]:
    """Part name -> #RRGGBB read back from the GLB's JSON chunk."""
    data = path.read_bytes()
    length = int.from_bytes(data[12:16], "little")
    gltf = json.loads(data[20 : 20 + length])
    colours: dict[str, str] = {}
    for mesh in gltf["meshes"]:
        for prim in mesh["primitives"]:
            pbr = gltf["materials"][prim["material"]]["pbrMetallicRoughness"]
            r, g, b, _ = pbr["baseColorFactor"]
            colours[mesh["name"]] = "#{:02X}{:02X}{:02X}".format(
                *(round(c * 255) for c in (r, g, b))
            )
    return colours


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, StubClient]:
    base = tmp_path_factory.mktemp("generate-course")
    first_client = StubClient()
    assert _run(base / "a", first_client) == cli.EXIT_OK
    assert _run(base / "b") == cli.EXIT_OK
    return base / "a", base / "b", first_client


@pytest.mark.geometry
@pytest.mark.slow
def test_writes_glbs_placements_and_report(
    two_runs: tuple[Path, Path, StubClient],
) -> None:
    out, _, client = two_runs
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "hills.glb",
        "lake-quinsig-boathouse.glb",
        "placements.json",
        "reed-bank.glb",
        "report.json",
    ]

    for glb in out.glob("*.glb"):
        data = glb.read_bytes()
        assert data[:4] == b"glTF"
        assert len(gzip.compress(data, mtime=0)) <= course_gen.GZIP_CAP_BYTES

    # Every mesh in every GLB carries a flat colour, and it is the one the
    # report says was chosen.
    report = json.loads((out / "report.json").read_text())
    for m in report["models"]:
        expected = {p["name"]: p["colour"] for p in m["parts"]}
        assert _glb_colours(out / m["filename"]) == expected

    placements = json.loads((out / "placements.json").read_text())
    assert isinstance(placements, list) and len(placements) == 3
    for entry in placements:
        assert set(entry) == _PLACEMENT_KEYS
        assert _MODEL_ID_RE.match(entry["modelId"])
        assert (out / entry["filename"]).is_file()
        assert entry["bankSide"] in ("left", "right", "centre")
        assert 0 <= entry["distanceAlongRouteMeters"] <= 5306
        assert -360 <= entry["orientationDegrees"] <= 360
        assert entry["scale"] > 0
        assert isinstance(entry["lateralOffsetMeters"], float)
    by_id = {p["modelId"]: p for p in placements}
    assert by_id["r-1-reed-bank"]["distanceAlongRouteMeters"] == 5306.0
    assert by_id["r-1-hills"]["bankSide"] == "centre"

    assert report["model"] == "stub/model-1"
    assert report["seed"] == 7
    assert report["llmCadSha"]
    assert report["failures"] == []
    hills = next(m for m in report["models"] if m["slug"] == "hills")
    assert hills["repaired"] is True
    for m in report["models"]:
        assert m["glbBytes"] == (out / m["filename"]).stat().st_size
        assert 0 < m["glbGzipBytes"] <= report["gzipCapBytes"]

    # The seed and model reach the model on every call, repair included.
    assert {(model, seed) for _, model, seed in client.calls} == {("stub/model-1", 7)}


@pytest.mark.geometry
@pytest.mark.slow
def test_same_seed_gives_identical_bytes(
    two_runs: tuple[Path, Path, StubClient],
) -> None:
    a, b, _ = two_runs
    names = sorted(p.name for p in a.iterdir())
    assert names == sorted(p.name for p in b.iterdir())
    for name in names:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


@pytest.mark.geometry
@pytest.mark.slow
def test_max_models_and_per_model_failures(tmp_path: Path) -> None:
    plan = json.loads(json.dumps(PLAN))
    plan["models"][0]["placement"]["bankSide"] = "upstream"  # unusable
    out = tmp_path / "out"
    assert _run(out, StubClient(plan), "--max-models", "2") == cli.EXIT_OK
    report = json.loads((out / "report.json").read_text())
    assert report["maxModels"] == 2
    slugs = [m["slug"] for m in report["models"]]
    assert slugs == ["lake-quinsig-boathouse", "reed-bank"]
    assert report["failures"][0]["modelId"] == "r-1-lake-quinsig-boathouse"
    assert "InvalidPlacement" in report["failures"][0]["error"]
    assert not (out / "lake-quinsig-boathouse.glb").exists()
    placements = json.loads((out / "placements.json").read_text())
    assert [p["modelId"] for p in placements] == ["r-1-reed-bank"]
