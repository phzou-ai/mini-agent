# Vermay Web

This directory contains the standalone Next.js frontend for Vermay. It is
kept in the same repository as the backend so the A2A routes, session/task
contracts, inspector UI, and approval workflow can evolve together.

## Development

Start the backend from the repository root:

```bash
vermay serve
```

Start the web UI from this directory:

```bash
pnpm install
pnpm dev
```

The frontend proxies to `http://127.0.0.1:8000` by default. Override the backend
base URL with:

```bash
VERMAY_API_BASE=http://127.0.0.1:8000 pnpm dev
```

## Production Build

The frontend and backend remain separate runtime processes. Build and start the
frontend with:

```bash
pnpm install --frozen-lockfile
pnpm build
VERMAY_API_BASE=http://127.0.0.1:8000 pnpm start
```

`VERMAY_API_BASE` is read by the server-side BFF. The frontend is a private
application package and is not published to npm. The backend does not serve the
generated Next.js bundle.

## Checks

```bash
pnpm typecheck
pnpm build
pnpm test:regression
pnpm test:e2e
```

`pnpm test:regression` starts an isolated Next development server using a
dedicated build directory, so it can run while the normal `pnpm dev` server is
active. `pnpm test:e2e` is the broader live suite and requires configured
backend dependencies.

The web app is intentionally agent-specific. Reusable UI primitives should only
be extracted later if another non-agent product actually needs them.

Assistant output supports GitHub Flavored Markdown and KaTeX math using `$...$`,
`$$...$$`, `\\(...\\)`, and `\\[...\\]` delimiters. The migration regression
suite owns structured-content rendering and horizontal-overflow behavior.
