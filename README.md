# Form Agent

An AI agent, packaged as a Chrome extension, that fills web forms for you from
a profile built out of your own documents.

Upload a resume, ID or any document, review the profile it extracts, open a
form and click **Fill this form**. The agent fills the page top to bottom with
a live progress card. It handles multi-page wizards, custom dropdowns, date
masks and the site's own validation errors, and asks you only about what it
genuinely can't fill. You can correct anything by chatting ("set state to
Karnataka", "shorten the address to 50 characters").

It **never submits a form**, never types passwords, OTPs or captchas, and never
dismisses a pop-up by agreeing to it. You review and submit yourself.

```
Chrome extension (apps/extension)  ⇄  local backend (services/agent_backend)  ⇄  AI model
reads the page, types and clicks,     decides what goes where, enforces          OpenAI, Claude on
checks every result, side panel       the safety rules, remembers sites          Bedrock, local, or none
```

For how it works inside, see `docs/ARCHITECTURE.md`.

---

## 1. Prerequisites

| Tool | Version | How to get it |
|---|---|---|
| **Git** | any | https://git-scm.com |
| **uv** (installs Python 3.12 for you) | 0.4 or later | macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh` · Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| **Node.js** | 20 or later | https://nodejs.org (LTS) |
| **pnpm** | 11.x | `npm install -g pnpm` |
| **Google Chrome** | 116 or later | https://www.google.com/chrome |
| **An AI model** | one of the options in step 3 | an OpenAI API key is the simplest |

Check them:

```bash
git --version && uv --version && node --version && pnpm --version
```

---

## 2. Get the code and install

```bash
git clone https://github.com/MO-Siddhant-44490/form_agent_codex.git
cd form_agent_codex

uv sync --all-packages --all-extras               # Python backend (creates .venv)
pnpm install                                      # JavaScript dependencies
pnpm --filter @form-agent/extension build:dev     # builds the extension into apps/extension/dist
```

`build:dev` lets the extension work on any website. The plain `build` limits it
to `localhost` and is used for tests.

---

## 3. Choose a model

Copy the settings template and fill in **one** option:

```bash
cp .env.example .env          # Windows: copy .env.example .env
```

`.env` stays on your machine; git ignores it.

### Option A: OpenAI (simplest)

Get a key at https://platform.openai.com/api-keys, then put it in `.env`:

```
OPENAI_API_KEY=sk-...
```

That's all: a key on its own selects OpenAI. The default model is `gpt-4.1`.
To use another one, add `OPENAI_MODEL=gpt-4o` (or any chat model that accepts
images and PDFs; document upload needs that). `OPENAI_BASE_URL` points it at any
OpenAI-compatible API instead.

### Option B: Claude on Amazon Bedrock

Needs an AWS account with Bedrock access to an Anthropic Claude model.

```
MODEL_PROVIDER=bedrock
AWS_PROFILE=your-profile
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-20250514-v1:0
```

Log in first (e.g. `aws sso login --profile your-profile`). To list the model
IDs your account can use:

```bash
uv run python -m agent_backend.model_gateway.list_models
```

### Option C: a local model

Any OpenAI-compatible server, such as Ollama, vLLM or LM Studio:

```
MODEL_PROVIDER=local
LOCAL_MODEL_URL=http://localhost:11434/v1
LOCAL_MODEL_ID=llama3.1
```

Document upload needs a model that reads images and PDFs.

### Option D: no model (offline demo)

```
MODEL_PROVIDER=fake
```

The whole flow works, but matching is basic keyword matching and document
upload can't read files. Good for trying the extension, not for real forms.

---

## 4. Start the backend

From the repo root (it reads `.env` from there and keeps its memory in
`./_agent_state/`):

```bash
uv run --package agent-backend --extra server python -m agent_backend.api.server
```

Leave this terminal open. The output should include:

```
model provider: ModelAssistedMapper (gpt-4.1)
model credentials: ok
Uvicorn running on http://127.0.0.1:8000
```

If it prints `WARNING`, the line after it says what to fix (usually the key or
the AWS login). You can check at any time:

```bash
curl http://127.0.0.1:8000/health      # "model_ready": true
```

---

## 5. Install the Chrome extension

1. Open Chrome and go to **`chrome://extensions`**.
2. Turn on **Developer mode** (toggle, top-right).
3. Click **Load unpacked** (top-left).
4. Choose the folder **`form_agent_codex/apps/extension/dist`** (the build from
   step 2) and click **Select**.
5. **Form Agent (dev)** appears in the list. Make sure its toggle is on.
6. Pin it: click the puzzle-piece icon in Chrome's toolbar, then the pin next to
   **Form Agent (dev)**.

After you pull new code or rebuild, go back to `chrome://extensions` and click
the **reload ↻** icon on the Form Agent card.

---

## 6. Fill a form

1. Open any form page (for example a registration or application form).
2. Click the **Form Agent** icon in the toolbar. The side panel opens on the right.
3. **Build your profile.** Do one of these:
   - Click **📄 Upload a document**: a resume, ID card or a PDF/image with your
     details. The agent reads it and lists what it found. Review it, edit any
     line, and add fields the form needs.
   - Or type one `key: value` per line, e.g. `full_name: Asha Rao`.
4. Click **Fill this form**. A progress card shows each field as it is filled,
   and the page scrolls to and outlines each field.
5. **Answer what it asks** in the chat. You can tap a suggested option, type a
   value, or tell it in plain words ("my mobile is 98xxxxxxx", "tick it").
   Answering continues the form, including pressing *Next* on multi-step forms.
6. Correct anything by chatting: "set state to Karnataka", "change it back",
   "shorten the address to fit".
7. **Review the form yourself and submit it.** The agent never does.

Things that are always left to you: passwords, OTPs, captchas, consent boxes
(you get **Tick it / Leave it**), and the final Submit.

---

## 7. Run the tests (optional)

```bash
uv run pytest                                   # backend + contracts, fully offline
pnpm --filter @form-agent/extension test        # extension unit tests
./packages/contracts/check_drift.sh             # generated types match the Python models
```

Browser integration tests drive the real extension in Chromium against local
test pages:

```bash
npx playwright install chromium                 # once
pnpm --filter @form-agent/extension build:dev
RUN_EXTENSION_INTEGRATION=1 uv run pytest services/agent_backend/tests/test_extension_integration.py
```

Tests never call a live model and need no API key.

---

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Panel says "the model is unreachable" | Check the backend's startup `WARNING`: a wrong or missing `OPENAI_API_KEY`, or an expired AWS login. Fix `.env`, restart the backend. |
| `401 Incorrect API key` | The OpenAI key in `.env` is wrong or revoked. |
| `CERTIFICATE_VERIFY_FAILED` (corporate network) | Your network intercepts HTTPS. Point Python at your company's CA bundle: `export SSL_CERT_FILE=/path/to/ca-bundle.pem` before starting the backend. For `npm`/`pnpm`/Playwright downloads use `NODE_EXTRA_CA_CERTS` the same way. |
| Clicking Fill does nothing | Is the backend terminal still running? Reload the extension in `chrome://extensions`, then reload the form page. |
| Works on `localhost` only | You loaded a `build`, not `build:dev`. Run `pnpm --filter @form-agent/extension build:dev` and reload the extension. |
| Port 8000 is taken | Stop the other program. The extension expects the backend at `127.0.0.1:8000`. |
| A site shows "Access Denied" in the test browser | The site blocks automated browsers. Use your normal Chrome; the agent never bypasses bot protection. |
| A field was left empty | It is either optional with nothing matching in your profile (listed as "left blank"), or the agent asked about it in the chat. Add the value to your profile and click Fill again. |

---

## For contributors

- Read **`AGENTS.md`** before changing code. It lists the safety rules every
  change must keep: never submit, never handle secrets, verify every action, and
  more.
- Message types live in `packages/contracts/python` (Pydantic). After changing
  them, regenerate the schemas and TypeScript types (commands in `AGENTS.md`);
  CI fails on drift.
- Code map:

  | Where | What |
  |---|---|
  | `apps/extension/src/perception` | reads the page into typed fields |
  | `apps/extension/src/actions` | types and clicks |
  | `apps/extension/src/verification` | checks each result |
  | `apps/extension/src/sidepanel` | the chat UI |
  | `services/agent_backend/agent_backend/driver.py` | the fill loop |
  | `services/agent_backend/agent_backend/mapper.py` | which profile value goes in which field |
  | `services/agent_backend/agent_backend/adapt.py` | reshapes values to each field's format |
  | `services/agent_backend/agent_backend/policy.py` | the safety gate |
  | `services/agent_backend/agent_backend/model_gateway` | OpenAI / Bedrock / local / fake |
