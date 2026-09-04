# Using the Form Agent (the product flow)

This is how a person actually uses it — load the extension in **your own
Chrome**, open a form, and click **Fill this form** in the side panel. It fills
the form in your tab, shows you its questions, and **never submits** (you review
and submit yourself).

## 1. Start the backend

The backend does the mapping (Bedrock) and orchestration.

```bash
cd form_agent_codex
aws sso login --profile dev                      # for Bedrock (token lasts a few hours)
AWS_PROFILE=dev MODEL_PROVIDER=bedrock \
  uv run python -m agent_backend.api.server
```

Leave it running — it prints the model it resolved (e.g. `ModelAssistedMapper
(apac.anthropic.claude-sonnet-4-...)`). It binds to `127.0.0.1:8000`.

To run without Bedrock (deterministic name-matching only): `MODEL_PROVIDER=none`.

## 2. Build the extension for the sites you'll use

The committed manifest only permits `localhost`. To use it on a real form, add
that site's host to `apps/extension/manifest.json` → `host_permissions`
(e.g. `"https://httpbin.org/*"`), or `"<all_urls>"` for any site while testing.
Then build:

```bash
pnpm --filter @form-agent/extension build
```

## 3. Load the extension in Chrome

1. Open `chrome://extensions`
2. Turn on **Developer mode** (top right)
3. **Load unpacked** → select `apps/extension/dist`

## 4. Fill a form

1. Open any form in a tab.
2. Click the **Form Agent** toolbar icon → the **side panel** opens.
3. Your profile is prefilled as `key: value` lines — edit it for the person
   whose form this is (or paste new values).
4. Click **Fill this form**.
5. Watch it fill **this tab**, field by field. When done, the panel shows:
   - how many fields were filled and verified, and
   - any **questions** it needs you to handle (a CAPTCHA, an ambiguous
     dropdown, a field with no data).

It **never submits** — review the filled form and submit it yourself. Login
pages and CAPTCHAs are always left for you (invariant: credentials/CAPTCHA are
never automated).
