"""Every third-party module `src/` imports must be a declared dependency.

`matplotlib`, `scipy` and `pillow` were imported at runtime by
`render.py` and `validate.py` but declared nowhere: they resolved only
because `cadquery -> vtk -> matplotlib` and `cadquery -> scipy` happened
to pull them in. A VTK restructure would have broken rendering and
validation at runtime, in a user's session, with an ImportError — the two
features the product is built around (CAD-012 / #13).
"""
from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "cad_mcp"

# Import name -> distribution name, where they differ.
DIST_ALIASES = {
    "PIL": "pillow",
    "OCP": "cadquery",  # ships as cadquery-ocp, pulled in by cadquery
    "cq": "cadquery",
    "yaml": "pyyaml",
    "pyrender": "pyrender",
    "OpenGL": "PyOpenGL",
    "mpl_toolkits": "matplotlib",
    "cadquery": "cadquery",
}

# Modules that are optional by design and guarded at the call site.
OPTIONAL = {"pyrender", "OpenGL"}


def _pyproject() -> dict[str, object]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _declared() -> set[str]:
    data = _pyproject()
    project = data["project"]
    assert isinstance(project, dict)

    names: set[str] = set()
    specs = list(project.get("dependencies") or [])
    for group in (project.get("optional-dependencies") or {}).values():
        specs.extend(group)
    dev = (data.get("dependency-groups") or {}).get("dev") or []
    specs.extend(dev)

    for spec in specs:
        # "mcp[cli]>=2.0,<3" -> "mcp"
        name = spec.split("[")[0]
        for sep in (">=", "<=", "==", "!=", "~=", ">", "<", ";", " "):
            name = name.split(sep)[0]
        names.add(name.strip().lower().replace("_", "-"))
    return names


def _top_level_imports() -> dict[str, set[str]]:
    """Top-level module name -> the files that import it."""
    found: dict[str, set[str]] = {}
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:  # relative
                    continue
                mods = [node.module or ""]
            else:
                continue
            for mod in mods:
                top = mod.split(".")[0]
                if top:
                    found.setdefault(top, set()).add(
                        str(path.relative_to(ROOT))
                    )
    return found


def _is_stdlib(name: str) -> bool:
    return name in sys.stdlib_module_names


def test_every_third_party_import_is_declared() -> None:
    declared = _declared()
    undeclared: dict[str, set[str]] = {}

    for top, files in _top_level_imports().items():
        if top == "cad_mcp" or _is_stdlib(top):
            continue
        dist = DIST_ALIASES.get(top, top).lower().replace("_", "-")
        if dist not in declared:
            undeclared[top] = files

    assert not undeclared, (
        "These modules are imported by src/ but not declared in "
        "pyproject.toml:\n"
        + "\n".join(
            f"  {mod} (in {', '.join(sorted(files))})"
            for mod, files in sorted(undeclared.items())
        )
    )


def test_optional_imports_are_in_an_extra_not_core() -> None:
    """pyrender must not become a hard requirement by accident."""
    data = _pyproject()
    project = data["project"]
    assert isinstance(project, dict)
    core = " ".join(project.get("dependencies") or [])
    extras = project.get("optional-dependencies") or {}

    assert "pyrender" not in core, (
        "pyrender is optional (matplotlib is the supported default); "
        "keep it in the 'gpu' extra."
    )
    assert "gpu" in extras, "the 'gpu' extra is missing"
    assert any("pyrender" in s for s in extras["gpu"])


def test_version_sensitive_pins_have_upper_bounds() -> None:
    """CLAUDE.md's 'don't upgrade cadquery/OCP casually', enforced."""
    data = _pyproject()
    project = data["project"]
    assert isinstance(project, dict)
    specs = {
        s.split("[")[0].split(">")[0].split("<")[0].split("=")[0].strip(): s
        for s in (project.get("dependencies") or [])
    }

    for name in ("cadquery", "mcp", "trimesh", "manifold3d"):
        spec = specs.get(name)
        assert spec, f"{name} is not a declared dependency"
        assert "<" in spec, (
            f"{name} has no upper bound ({spec!r}). Rendering and export "
            f"are version-sensitive; an unbounded range lets a breaking "
            f"release in unannounced."
        )


def test_mcp_is_pinned_to_the_v2_api() -> None:
    """The code uses MCPServer and input_schema, both v2-only."""
    data = _pyproject()
    project = data["project"]
    assert isinstance(project, dict)
    spec = next(
        s for s in project["dependencies"] if s.startswith("mcp")
    )
    assert ">=2" in spec, (
        f"mcp must be pinned to v2 ({spec!r}); CLAUDE.md requires the "
        f"MCPServer API, which does not exist in v1."
    )


@pytest.mark.parametrize("module", sorted(OPTIONAL))
def test_optional_modules_are_imported_lazily(module: str) -> None:
    """An optional dependency must not be imported at module scope."""
    offenders = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module scope only
            if isinstance(node, ast.Import) and any(
                a.name.split(".")[0] == module for a in node.names
            ):
                offenders.append(str(path.relative_to(ROOT)))
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == module
            ):
                offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, (
        f"{module} is optional but imported at module scope in "
        f"{offenders}; import it inside the function that needs it."
    )
