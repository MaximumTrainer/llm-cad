# syntax=docker/dockerfile:1
#
# cad-mcp as a remote MCP server (SPEC 10.4).
#
# This image needs an ordinary Linux userspace, not an edge runtime:
# cadquery binds OCCT (a native C++ B-rep kernel), sandbox.py spawns a
# subprocess per execution and caps it with setrlimit, and rendering
# wants an offscreen GL stack. None of that survives WASM.
#
# Build:  docker build -t cad-mcp .
# Run:    docker run --rm -p 8000:8000 \
#             -e CAD_MCP_AUTH_TOKEN=dev-token \
#             -e CAD_MCP_ALLOWED_HOSTS=127.0.0.1:8000,localhost:8000 \
#             cad-mcp

# ------------------------------------------------------------------
# Stage 1: resolve dependencies into a self-contained virtualenv.
# ------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.7.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies resolve from the committed lockfile and are cached
# separately from the source, so editing a tool does not re-download a
# gigabyte of OCCT. `--frozen` means the image gets exactly the versions
# CI tested; CLAUDE.md is explicit that the cadquery/OCP pins are not to
# float.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ------------------------------------------------------------------
# Stage 2: runtime. No build toolchain, no uv, no source of truth but
# the virtualenv we just built.
# ------------------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# libglib2.0-0, libgomp1 and the X client libs are OCCT/VTK's own
# shared-library needs: without them `import cadquery` fails at load
# time, which is a far more confusing failure than one at first use.
#
# libEGL/libGL and Mesa's software rasteriser are for the *optional*
# pyrender path. This image installs runtime dependencies only, so the
# `gpu` extra is absent and the active backend is the matplotlib
# fallback -- supported by SPEC N5, and what CLAUDE.md calls the default.
# They are kept so that adding `--extra gpu` is a one-line change rather
# than a debugging session, and because VTK links some of them anyway.
# docs/deploy.md says how to check which backend is live (SPEC 10.4 A8).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libegl1 \
        libgl1 \
        libgl1-mesa-dri \
        libglib2.0-0 \
        libgomp1 \
        libxext6 \
        libsm6 \
        libx11-6 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Not root. The sandbox's containment is defence in depth (SPEC N1's
# threat model is explicit that it is not a jail for hostile code), so
# the uid the whole process tree runs as is doing real work here.
RUN useradd --create-home --uid 10001 cad

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # EGL rather than GLX: there is no X server here. render.py falls
    # back to matplotlib if this does not initialise, which is supported
    # (SPEC N5) but visibly poorer -- /health reports which one is live.
    PYOPENGL_PLATFORM=egl \
    CAD_MCP_TRANSPORT=http \
    CAD_MCP_HOST=0.0.0.0 \
    CAD_MCP_PORT=8000 \
    # Exports must land somewhere writable by uid 10001 and outside the
    # session tmpdir, which reset_session deletes (SPEC G4).
    CAD_MCP_OUTPUT_DIR=/data/output

WORKDIR /app
COPY --from=builder --chown=cad:cad /app/.venv /app/.venv
COPY --from=builder --chown=cad:cad /app/src /app/src

RUN mkdir -p /data/output && chown -R cad:cad /data

USER cad

EXPOSE 8000

# Importing cadquery takes ~3.3s, so a container that is *running* is not
# yet a container that can serve. start-period covers that; the server
# pre-warms a sandbox worker before it binds the port, so once /health
# answers, the kernel really is loaded.
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["cad-mcp", "--transport", "http"]
