# Deploying cad-mcp as a remote MCP server

`cad-mcp` runs locally over stdio with no setup. This page is about the
other mode: a single hosted instance that any MCP client can reach over
HTTPS, so the model does not have to be installed on every machine that
wants to design something.

Two things make this harder than putting a web app behind a load
balancer, and both shape everything below:

1. **The server needs a real Linux host.** `cadquery` binds OCCT, a
   native C++ kernel of roughly a gigabyte of shared objects; `sandbox.py`
   spawns a subprocess per execution and caps it with `setrlimit`; and
   rendering wants an offscreen GL stack. Edge runtimes that run Python
   as WebAssembly cannot do any of that.
2. **MCP's streamable HTTP transport is stateful.** The server issues an
   `Mcp-Session-Id`, and every later request in that session must reach
   the same process, because the session's parts, code history and cached
   `.brep` live on one machine's local disk. Route the second request
   elsewhere and the model is not slow to find, it is gone.

---

## Run it in Docker

The container is the deployment unit, and it is worth running locally
first — every acceptance criterion except multi-machine routing can be
checked on a laptop.

```bash
docker build -t cad-mcp .

docker run --rm -p 8000:8000 \
  -e CAD_MCP_AUTH_TOKEN=dev-token \
  -e CAD_MCP_PUBLIC_URL=http://127.0.0.1:8000 \
  -e CAD_MCP_ALLOWED_HOSTS=127.0.0.1:8000,localhost:8000 \
  cad-mcp
```

Then exercise the real thing over the wire:

```bash
uv run python scripts/smoke_remote.py \
  --url http://127.0.0.1:8000 --token dev-token --repeat 10
```

That script is the gate, not a demo. It asserts a request without a
bearer token gets 401, that every SPEC 5.1 tool is present, and that
`execute_cad` followed by `render_views` — two separate HTTP requests —
returns actual PNG bytes. A deployment that comes up healthy and renders
nothing is the failure this is built to catch.

### Health, readiness, and which render backend is live

`GET /health` needs no token and reports **readiness**, not liveness:

```console
$ curl -i http://127.0.0.1:8000/health          # first ~3 seconds
HTTP/1.1 503 Service Unavailable
{"status":"starting","kernel":"starting","render_backend":"matplotlib","version":"0.1.0"}

$ curl -i http://127.0.0.1:8000/health          # thereafter
HTTP/1.1 200 OK
{"status":"ok","kernel":"ready","render_backend":"matplotlib","version":"0.1.0"}
```

The gap is the CadQuery import in the kernel worker. It matters because
the port opens before geometry can run, and a health check that cannot
tell those apart sends a client's first `execute_cad` to a machine with
no kernel behind it. `fly.toml` gives the check a 90s grace period for
the same reason.

`render_backend` answers the question SPEC N5 leaves open -- the render
*is* the product, so it should never be a surprise which backend is
running. `matplotlib` is this image's expected answer: it ships runtime
dependencies only, so the `gpu` extra is absent. `pyrender` means Mesa's
software EGL came up.

---

## Configuration

| Variable | Purpose |
|---|---|
| `CAD_MCP_TRANSPORT` | `http` to serve streamable HTTP. |
| `CAD_MCP_HOST` / `CAD_MCP_PORT` | The **bind** address. `0.0.0.0:8000` in a container. |
| `CAD_MCP_PUBLIC_URL` | The URL clients actually reach. See below. |
| `CAD_MCP_ALLOWED_HOSTS` | Comma-separated `Host` values to accept (SPEC 10.1 H6). |
| `CAD_MCP_AUTH_TOKEN` | Shared bearer token. **Secret.** |
| `MESHY_API_KEY` | Optional; `gen_ai_mesh` is disabled without it. **Secret.** |
| `PYOPENGL_PLATFORM` | `egl` for headless GL. |

### `CAD_MCP_PUBLIC_URL` is not optional in production

The bind address and the reachable URL are different things, and OAuth
protected-resource metadata needs the second. Without this variable a
server bound to `0.0.0.0` advertises `http://0.0.0.0:8000/mcp` as its
resource URL — a wildcard, not an address, and `http` rather than the
`https` origin the client used. The server warns at start-up when it is
bound off-loopback without one.

### `CAD_MCP_STATELESS=1` is not a way to avoid session affinity

It breaks the core loop outright: `render_views` and `export_model` read
state that a previous `execute_cad` wrote.

---

## Session affinity

Clients know nothing about the hosting platform and will not send routing
headers, so affinity is established server-side, in `src/cad_mcp/fly.py`.

The session id handed to the client is `<machine-id>~<sdk-id>`. The
wrapper is stripped again before the request reaches the MCP app, which
therefore never learns it happened. Any instance can then read the owner
straight off the header, with no shared store and no lookup:

- **owned here** → strip the wrapper and serve;
- **owned elsewhere** → reply with `fly-replay: instance=<id>`, and the
  proxy transparently re-runs the request on the right machine;
- **owner gone** (the request already carries `fly-replay-src`, so
  bouncing again would loop) → a JSON-RPC `-32600` telling the client to
  re-initialize. Not a 500, and not a silently empty session.

Off the platform, `FLY_MACHINE_ID` is unset and the middleware is a
pass-through, so local runs are unchanged.

`tests/test_fly_affinity.py` drives all of this as raw ASGI, so the
routing logic is tested without a container or a network. What those
tests *cannot* show is that a real proxy honours the header — that needs
two machines actually running, and is the one criterion that stays open
until a deployment exists.

---

## Fly.io

`fly.toml` is committed and configured for a `cad-mcp` app. Bootstrap is
a one-off:

```bash
fly auth login
fly apps create cad-mcp
fly secrets set --app cad-mcp \
  CAD_MCP_AUTH_TOKEN="$(openssl rand -hex 32)" \
  MESHY_API_KEY=...            # optional

# A token scoped to this app, not a personal org-wide one.
fly tokens create deploy -a cad-mcp
gh secret set FLY_API_TOKEN --env production   # paste the token
gh secret set CAD_MCP_AUTH_TOKEN --env production
```

Setting a secret triggers a rolling restart, which ends live sessions.

A staging app is the same recipe with `cad-mcp-staging`, and is deployable
from the Actions tab via `workflow_dispatch` with `target: staging`.

### Sizing

`shared-cpu-2x` with 4 GB. `sandbox.py` alone caps one execution at 2 GiB
RSS by default, so a 2 GB machine would leave the server itself nothing.
If SPEC N2's ≤2s render budget is missed on shared vCPUs, move to a
`performance-*` size and record the cost delta rather than quietly
accepting slower renders — the render is the product.

### Autostop and cold start

`auto_stop_machines = "suspend"` with `min_machines_running = 0`. Suspend
snapshots RAM, so a resumed machine already has `cadquery` imported,
which is the single biggest lever on cold start: the import alone is
~3.3s. It also makes it much less likely that a machine stops mid-session
and takes a session's `.brep` cache with it.

If a machine *is* replaced mid-session, code history is the source of
truth (SPEC §7): the client re-initializes and replays its code. Exports
already written are lost with the machine's disk, which is why they
should be fetched when produced rather than left to accumulate.

### Deploying

`git push` to `main` deploys, via `.github/workflows/deploy.yml`. The
jobs gate in order — tests, then a locally built-and-smoked container,
then the deploy, then a smoke test against the live URL. A red suite
cannot reach production and a broken render cannot be reported green.

### Rolling back

```bash
fly releases list --app cad-mcp
```

Then either re-run the deploy workflow with `ref` set to the last good
git SHA, or roll the image back directly:

```bash
fly deploy --app cad-mcp --image <registry.fly.io/cad-mcp:deployment-...>
```

The workflow's `ref` input exists precisely so a rollback is the same
reviewed path as a deploy rather than an improvised `flyctl` invocation.

---

## Connecting a client

```json
{
  "mcpServers": {
    "cad-mcp": {
      "type": "http",
      "url": "https://cad-mcp.fly.dev/mcp",
      "headers": { "Authorization": "Bearer <CAD_MCP_AUTH_TOKEN>" }
    }
  }
}
```

---

## Cost

Machines bill for running time, so with `min_machines_running = 0` an
idle deployment costs approximately storage only. A `shared-cpu-2x`/4 GB
machine is roughly $0.03/hr while awake; an hour of modelling a day is
therefore a couple of dollars a month, and a machine left awake
permanently is around $20. Cap scale-out to keep a runaway client from
turning that into a surprise.

## What the container run actually showed

Verified against the image, not asserted. Three bugs only a real Linux
container could have surfaced:

- **3MF export was dead.** trimesh needs `lxml` *and* `networkx` for it
  and declares neither; the dev virtualenv happened to have both, a
  runtime-only install had neither. `scripts/smoke.py` now exports every
  format SPEC G4 advertises, so the clean-install CI job catches a
  repeat.
- **The sandbox did not work on Linux at all.** `_PathPolicy.check`
  resolved paths with `os.path.realpath`, which on POSIX calls the very
  `os.lstat`/`os.readlink` the guard wraps, recursing until
  RecursionError. Writes *inside* the session directory failed too, and
  "blocked" meant a crash rather than a policy decision.
- **`RLIMIT_NPROC = 64` killed the worker before it ran a line.** That
  limit is per real UID on Linux and counts threads, and NumPy/OpenBLAS
  and OCCT each start a pool sized from the CPU count. The worker died
  during `import numpy`. The brake now scales with `os.cpu_count()`.

Measured in the container on a 12-core development machine, matplotlib
backend:

| | measured | SPEC N2 budget |
|---|---|---|
| `render_views`, warm | 0.40s / 0.41s | ≤2s |
| `execute_cad`, cold kernel | 3.21s | ≤5s |
| `validate_mesh` | 0.07s | ≤5s |
| container start → `/health` 200 | 4–6s | — |
| image size | 2.08 GB | — |

`scripts/smoke_remote.py --repeat 10` passes end to end: 401 without a
token, all 14 SPEC 5.1 tools listed, 10/10 iterations returning a PNG
render, and all four export formats written. N1 holds inside the
container — network egress from user code blocked, writes outside the
session directory denied with no file created, wall-clock timeout
enforced.

## Status

Not yet deployed. Everything above is built and verified against a local
container; what needs a Fly account is the rest of issue #1's A1–A8 —
latency and cold start *on the platform*, `fly-replay` proven with more
than one machine actually running, and a rollback performed once. Until
then, treat the Fly-specific numbers in this document as configuration
intent rather than measurements. The routing logic itself is covered by
`tests/test_fly_affinity.py`, which drives it as raw ASGI.
