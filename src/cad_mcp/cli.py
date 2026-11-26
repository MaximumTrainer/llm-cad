"""``llm-cad`` command-line entry point (SPEC 10.5).

Subcommands:

``generate-course``
    Turn a rownative course JSON into scenery GLBs, ``placements.json``
    and ``report.json`` with no MCP host in the loop. Built for
    virtualrow's monthly scenery workflow (issue #42).

Exit codes for ``generate-course``:

* ``0`` -- at least one GLB was written;
* ``1`` -- the run completed but produced nothing usable (``report.json``
  says why);
* ``2`` -- the run could not start: bad arguments, no API key, an
  unreadable course file. Nothing is written.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from cad_mcp import course_gen
from cad_mcp.llm_client import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    ModelClient,
    OpenAICompatibleClient,
    api_key_from_env,
)

EXIT_OK = 0
EXIT_NOTHING_USABLE = 1
EXIT_USAGE = 2


def _positive_int(text: str) -> int:
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-cad",
        description="Headless llm-cad commands. The MCP server is `cad-mcp`.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser(
        "generate-course",
        help="generate scenery GLBs and placements for one rownative course",
        description=(
            "Generate scenery GLBs, placements.json and report.json for one "
            f"rownative course. Reads the model API key from {API_KEY_ENV}. "
            "Exit 0 when at least one GLB is written, 1 when nothing usable "
            "was produced, 2 when the run could not start."
        ),
    )
    gen.add_argument(
        "--course-json", required=True, type=Path, metavar="PATH",
        help="path to the rownative course JSON",
    )
    gen.add_argument(
        "--out", required=True, type=Path, metavar="DIR",
        help="directory to write <slug>.glb, placements.json and report.json",
    )
    gen.add_argument(
        "--model", default=DEFAULT_MODEL, metavar="ID",
        help=f"model id for the chat-completions API (default: {DEFAULT_MODEL})",
    )
    gen.add_argument(
        "--seed", type=int, default=0, metavar="INT",
        help="sampling seed passed to the model (default: 0)",
    )
    gen.add_argument(
        "--max-models", type=_positive_int, default=3, metavar="N",
        help="most models to generate (default: 3)",
    )
    return parser


def generate_course(
    args: argparse.Namespace, client: ModelClient | None = None
) -> int:
    if client is None:
        key = api_key_from_env()
        if key is None:
            print(
                f"llm-cad: {API_KEY_ENV} is not set; refusing to run. "
                "Nothing was written.",
                file=sys.stderr,
            )
            return EXIT_USAGE
        client = OpenAICompatibleClient(key)

    try:
        course = course_gen.load_course(args.course_json)
    except course_gen.CourseError as exc:
        print(f"llm-cad: {exc}. Nothing was written.", file=sys.stderr)
        return EXIT_USAGE

    result = course_gen.generate(
        course,
        args.out,
        client=client,
        model=args.model,
        seed=args.seed,
        max_models=args.max_models,
    )

    for outcome in result.outcomes:
        if outcome.filename:
            print(f"wrote {outcome.filename} ({outcome.glb_gzip_bytes} B gzipped)")
        else:
            print(f"failed {outcome.slug}: {outcome.error}", file=sys.stderr)
    if result.plan_error:
        print(f"llm-cad: no plan from the model: {result.plan_error}", file=sys.stderr)

    if result.written:
        return EXIT_OK
    print(
        "llm-cad: no usable models were produced; see report.json.",
        file=sys.stderr,
    )
    return EXIT_NOTHING_USABLE


def main(
    argv: Sequence[str] | None = None, *, client: ModelClient | None = None
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "generate-course":
        return generate_course(args, client)
    return EXIT_USAGE  # pragma: no cover - argparse enforces the choices


def entrypoint() -> None:
    sys.exit(main())
