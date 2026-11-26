"""Default colours for generated scenery (SPEC 10.5 H8).

virtualrow's scenery kit is flat-coloured: every part of a model carries
one glTF PBR ``baseColorFactor`` from a fixed palette, no textures, and
the scene lights it (virtualrow ``public/assets/scenery/README.md``,
palette in ``scripts/build_scenery.py``). Generated models have to sit
beside those, so they use the same palette.

A part's colour is chosen in this order, and the source is recorded in
``report.json``:

1. ``model`` -- the hex the model gave for the part, when it is valid;
2. ``part-name`` -- the first palette keyword found in the part's name
   ("roof", "reeds", "parapet" ...);
3. ``role`` -- the default for the model's role (hero landmark, bank
   material, backdrop);
4. ``fallback`` -- a neutral weathered concrete.

So no part is ever exported uncoloured.
"""
from __future__ import annotations

import re

_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")

#: Keyword -> hex, checked in order, so more specific words come first
#: ("roof" before "house", "trunk" before "tree"). Hexes are virtualrow
#: kit colours, named after the constant they come from.
KEYWORD_COLOURS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("window", "glaz", "glass"), "#2A3A44"),  # GLAZE
    (("door", "bay", "opening"), "#241E18"),  # BAY_DK
    (("roof", "slate", "shingle-roof"), "#4A3F35"),  # ROOF_NE
    (("cupola", "balcony", "balustrade", "trim"), "#F0EDE4"),  # BALC
    (("flag",), "#C8102E"),  # FLAG_RED
    (("buoy",), "#E8452B"),  # BUOY_RED
    (("hull", "boat", "launch"), "#F2F2F0"),  # HULL_WHITE
    (("brick",), "#8B4A3A"),  # BRICK_UK
    (("render", "stucco", "plaster"), "#E8E0D0"),  # RENDER_UK
    (("clapboard", "siding", "boathouse", "house", "shed", "wall-panel"), "#C9BFA8"),
    (("parapet", "coping"), "#B8B0A0"),  # PARAPET
    (("arch", "voussoir"), "#A79E8E"),  # STONE_ARCH
    (("stone", "masonry", "quay", "embankment", "wall"), "#9A9184"),  # STONE_WALL
    (("concrete", "bridge", "pier", "abutment", "deck", "pontoon"), "#B0ACA4"),
    (("girder", "steel", "rail", "railing", "frame", "pylon", "mast"), "#3A4048"),
    (("pile", "post", "stake"), "#4A3B2A"),  # PILE
    (("timber", "wood", "plank", "dock", "jetty", "boardwalk", "bench"), "#8B7355"),
    (("reed-head", "seed-head"), "#C8BC9A"),  # REED_HEAD
    (("reed", "rush", "sedge"), "#A89A6E"),  # REED
    (("trunk", "bark", "branch"), "#5A4A3A"),  # OAK_BARK
    (("willow",), "#8AA85C"),  # WIL_FOL
    (("pine", "conifer", "fir", "spruce"), "#3A5E4A"),  # EWP_NDL
    (("tree", "foliage", "leaf", "leaves", "canopy", "hedge", "bush", "wood-land",
      "forest", "hill"), "#3E6B32"),  # OAK_FOL
    (("bramble", "shrub"), "#41663A"),  # BRAMBLE
    (("grass", "lawn", "meadow", "verge", "bank"), "#5E8A46"),  # GRASS
    (("rock", "boulder", "cliff", "crag"), "#7E7A72"),  # ROCK
    (("moss", "algae"), "#4A6B3A"),  # MOSS
    (("gravel", "shingle", "beach", "pebble"), "#A89E8E"),  # SHINGLE
    (("soil", "earth", "mud", "silt"), "#6B5540"),  # SOIL
)

#: Per-role defaults, used when neither the model nor the part name
#: gives a colour.
ROLE_COLOURS: dict[str, str] = {
    "hero-landmark": "#C9BFA8",  # CLAP_NE: muted clapboard
    "bank-material": "#5E8A46",  # GRASS
    "backdrop": "#3E6B32",  # OAK_FOL
}

FALLBACK_COLOUR = "#B5B0A6"  # CONCRETE


def normalise_hex(value: object) -> str | None:
    """``#RRGGBB`` in upper case, or None when *value* is not a colour."""
    if not isinstance(value, str):
        return None
    match = _HEX_RE.match(value.strip())
    return f"#{match.group(1).upper()}" if match else None


def keyword_colour(name: str) -> str | None:
    words = name.lower().replace("_", "-").replace(" ", "-")
    for keywords, hex_colour in KEYWORD_COLOURS:
        if any(k in words for k in keywords):
            return hex_colour
    return None


def resolve_colour(
    requested: object, part_name: str, role: str
) -> tuple[str, str]:
    """``(hex, source)`` for one part; see the module docstring."""
    hex_colour = normalise_hex(requested)
    if hex_colour:
        return hex_colour, "model"
    hex_colour = keyword_colour(part_name)
    if hex_colour:
        return hex_colour, "part-name"
    hex_colour = ROLE_COLOURS.get(role.strip().lower())
    if hex_colour:
        return hex_colour, "role"
    return FALLBACK_COLOUR, "fallback"


def rgba(hex_colour: str) -> list[float]:
    """glTF ``baseColorFactor`` (linear 0-1 floats, opaque) for a hex."""
    h = hex_colour.lstrip("#")
    return [int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)] + [1.0]


def palette_prompt() -> str:
    """The palette as a short list the model can pick from."""
    seen: dict[str, str] = {}
    for keywords, hex_colour in KEYWORD_COLOURS:
        seen.setdefault(hex_colour, keywords[0])
    return ", ".join(f"{name} {hex_colour}" for hex_colour, name in seen.items())
