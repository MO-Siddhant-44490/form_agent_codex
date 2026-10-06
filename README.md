# Form Agent

An agentic Chrome extension that fills web forms from a profile built out of
your own documents. Upload a resume or ID, review the extracted profile, click
**Fill this form**, and correct anything by chatting. It handles multi-page
wizards, custom dropdowns, date masks and site validation errors, asks you only
about what it genuinely can't fill, and **never submits a form**: you review
and submit yourself.

Three parts:

| Part | Where | What it does |
|---|---|---|
| Chrome extension | `apps/extension` | Reads the page, types and clicks, checks each result, side-panel chat |
| Local backend | `services/agent_backend` | Decides what to fill, calls the model, enforces the safety rules |
| Shared contracts | `packages/contracts` | The typed messages both sides speak |

Start with `docs/ARCHITECTURE.md` for how it works and `PROGRESS.md` for the
current state.

## Prerequisites

| Tool | Version | Install |
|---|---|---|
| Python | 3.12+ | via `uv` (below) |
| uv | 0.4+ | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node.js | 20+ | https://nodejs.org |
| pnpm | 11.x | `npm install -g pnpm` (or `corepack enable`) |
| Google Chrome | 116+ | |

For real use you also need **AWS credentials with Amazon Bedrock access** to an
Anthropic Claude model (see *Model* below). To just try it out, the offline
`fake` model needs nothing.

## Setup

```bash
git clone <repo-url> form_agent_codex
cd form_agent_codex

uv sync --all-packages --all-extras    # Python: backend + contracts + server
pnpm install                            # JavaScript: extension + contracts
pnpm --filter @form-agent/extension build:dev   # builds apps/extension/dist
```

`build:dev` lets the extension run on any website. The plain `build` limits it
to localhost.

## Run it

### 1. Start the backend

**Offline, no AWS** (to try the flow; mapping is basic keyword matching):

```bash
MODEL_PROVIDER=fake uv run --package agent-backend --extra server \
  python -m agent_backend.api.server
```

**With Claude on Amazon Bedrock** (the real thing):

```bash
aws sso login --profile <your-profile>            # or any AWS credentials
export AWS_PROFILE=<your-profile> AWS_REGION=<region>
export BEDROCK_MODEL_ID=<inference-profile-id>    # e.g. apac.anthropic.claude-sonnet-4-20250514-v1:0
uv run --package agent-backend --extra server python -m agent_backend.api.server
```

List the model IDs your account can use:

```bash
AWS_PROFILE=<your-profile> uv run python -m agent_backend.model_gateway.list_models
```

The server listens on `http://127.0.0.1:8000`. At startup it prints
`model credentials: ok`, or a `WARNING` explaining what to fix.
`curl http://127.0.0.1:8000/health` shows `model_ready`. Run it from the repo
root: its memory database lives in `./_agent_state/`.

### 2. Load the extension

1. Open `chrome://extensions` and turn on **Developer mode**.
2. Click **Load unpacked** and choose `apps/extension/dist`.
3. After any rebuild, click the extension's reload button.

### 3. Fill a form

1. Open a form page and click the Form Agent icon to open the side panel.
2. Click **📄 Upload a document**, or type `key: value` lines into the profile.
3. Click **Fill this form** and watch the progress card.
4. Answer what it asks in the chat, or correct anything, e.g. "set state to Karnataka".
5. Review the form and submit it yourself.

## Tests

```bash
uv run pytest                                    # backend + contracts (offline)
pnpm --filter @form-agent/extension test         # extension unit tests
./packages/contracts/check_drift.sh              # generated types match the source
```

Browser integration tests drive the real extension in Chromium against local
fixture pages:

```bash
npx playwright install chromium                  # once
pnpm --filter @form-agent/extension build:dev
RUN_EXTENSION_INTEGRATION=1 uv run pytest services/agent_backend/tests/test_extension_integration.py
```

Tests never call a live model.

## Before you change code

Read `AGENTS.md`. It lists the safety invariants every change must keep. For
example: never submit a form, never read or replay passwords, OTPs or captchas,
and verify every action against the page. Contract changes go in the Python
models in `packages/contracts/python` and are regenerated (see `AGENTS.md`);
CI fails on drift.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Panel says the model is unreachable | AWS session expired: log in again and restart the backend with `AWS_PROFILE`/`AWS_REGION` set |
| "Access to this model is not available" | Your AWS account lacks that model; pick one from `list_models` |
| Nothing happens on a site | Reload the extension after a rebuild; use the `build:dev` build for non-localhost sites |
| `npm`/`pnpm`/Playwright downloads fail behind a corporate proxy | Point `NODE_EXTRA_CA_CERTS` at the proxy's CA certificate |
| A site returns "Access Denied" to the test browser | It blocks automated browsers; use your normal Chrome. The agent never bypasses bot protection. |
