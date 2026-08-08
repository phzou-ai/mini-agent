# Runtime and Release Boundary

This document defines the supported runtime and release boundary for Vermay `0.1.x`. It describes what the repository currently guarantees and avoids implying deployment formats that are not maintained yet.

## Supported Distribution

The supported `0.1.x` distribution is a source checkout or source archive of this repository. It contains two independently built and operated applications:

| Component | Source | Runtime | Supported start command |
| --- | --- | --- | --- |
| Agent backend | `vermay/` | Python 3.11+ | `vermay serve` |
| Agent Console | `web/` | Node.js with pnpm | `pnpm build && pnpm start` |

The Python editable install provides the `vermay` command, but a standalone PyPI wheel is not currently a supported full-stack release artifact. The frontend remains a private application package and is not published to npm. The backend does not embed or serve the generated Next.js application.

Docker images, Kubernetes manifests, process supervision, TLS termination, and a single combined executable are outside the maintained `0.1.x` release boundary. They can be added when a concrete deployment target is selected.

## Development Runtime

Install backend runtime and development dependencies from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Start the backend:

```bash
vermay serve
```

Start the frontend in another terminal:

```bash
cd web
pnpm install --frozen-lockfile
pnpm dev
```

The Next.js BFF reads `VERMAY_API_BASE` on the server. This value is not exposed as a browser credential and defaults to `http://127.0.0.1:8000`.

## Production Runtime

Build and start the two applications separately:

```bash
# Backend process
source .venv/bin/activate
vermay serve --host 127.0.0.1 --port 8000
```

```bash
# Frontend process
cd web
pnpm install --frozen-lockfile
pnpm build
VERMAY_API_BASE=http://127.0.0.1:8000 pnpm start
```

The backend has no built-in authentication and binds to localhost by default. Do not expose it directly to an untrusted network. A production deployment that leaves the host must provide authentication, authorization, TLS, request limits, and network policy in a trusted reverse proxy or gateway.

The Web UI is also not an authorization boundary. Its BFF reduces browser coupling to backend routes, but it does not make an externally reachable backend safe by itself.

## Configuration, Secrets, and State

Tracked configuration may contain non-secret defaults and environment-variable references:

```text
config/models.json
config/mcp_servers.json
skills/*.md
```

Secrets and machine-specific values belong in the process environment or an untracked `.env` file. `.env.example` documents supported local variables without real values. API keys, SSH targets, private key paths, and credentials must not be added to tracked JSON files.

Persistent runtime state is local and must remain writable by the backend process:

```text
data/agent.sqlite
data/checkpoints/langgraph.sqlite
data/eval_runs/
data/skill_proposals/
traces/
```

`data/agent.sqlite` and the LangGraph checkpoint database form one logical runtime state boundary. Back them up and restore them together when resumable tasks must survive a deployment. Do not include runtime databases, traces, generated proposals, `.env`, frontend build output, or dependency directories in a source release.

The 2026-08-02 clean-slate storage cut intentionally retired the old
service/session database format. When moving from a pre-cut local checkout,
discard `data/agent.sqlite`, `data/checkpoints/langgraph.sqlite`, and any
`traces/langgraph_checkpoints.sqlite` together before starting the current
runtime. No data migration or checkpoint continuation is supported across that
boundary.

## Public Service Boundary

External agent clients use A2A JSON-RPC through `POST /rpc` and discover the agent through `GET /.well-known/agent-card.json`. `/api/*` routes are first-party management and diagnostic endpoints used by the Agent Console; they are not the public agent-to-agent integration contract.

The backend health endpoint is `GET /health`. A healthy HTTP process does not guarantee that the configured model, MCP servers, child agents, or SSH targets are available.

## Release Gate

Before creating a source release:

1. update the project version and release notes;
2. confirm that `.env` and runtime state are not tracked;
3. run `scripts/check_source_release.sh` from a clean working tree;
4. run any environment-dependent smoke tests required for the target deployment; and
5. create the release only from a reviewed, clean commit.

The source-release check enforces the repository boundary and runs the default full-stack regression gate. During local development, `ALLOW_DIRTY=1 scripts/check_source_release.sh` can validate an intentionally dirty worktree, but this override must not be used to create a release. The default regression gate is non-mutating with respect to tracked source files: it restores the generated `web/next-env.d.ts` routes import before exiting, including when a build or test fails. A clean worktree must therefore remain clean after the gate completes.

The default regression gate verifies backend behavior, frontend type safety, the production Next.js build, and deterministic browser behavior. Live model, MCP, child-agent, and SSH checks remain deployment-specific because they depend on external services and credentials.
