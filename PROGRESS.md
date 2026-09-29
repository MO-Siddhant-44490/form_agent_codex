# Form Agent — Work Summary

_Last updated: 2026-09-16. Branch `main`; 15 commits ahead of `origin/main` (not yet pushed)._

An agentic Chrome extension + local backend that fills web forms from a user
profile built out of their documents, driven by a chat interface. It never
submits a form — the human always reviews first.

---

## 1. Where things stand

| Area | Status |
|---|---|
| Core fill loop (perceive → map → gate → act → verify → recover) | ✅ Done, verified live on pgportal.gov.in |
| Failure-aware recovery (classified failures, distinct strategies) | ✅ Done |
| Cross-run episodic memory (revisit skips the model) | ✅ Done, durable SQLite |
| Structural grounding (`autocomplete`) + constrained routing | ✅ Done |
| Taint gate (sensitive values need a trusted binding) | ✅ Done |
| **Phase 1** — chat side panel, answer-and-resume, corrections | ✅ Done |
| **Reasoning chat** — LLM understands requests, targeted edits, undo, honesty | ✅ Done |
| **Phase 2** — upload a document → build/merge the profile | ✅ Done |
| VLM document extraction (layout-agnostic, form-aware) | ✅ Done |
| Multi-doc merge: dedup, new fields, conflict prompts, new-vs-add | ✅ Done |
| Close-the-loop: length enforcement + auto-repair of flagged fields | ✅ Done |
| Field **purpose** perception (credential / captcha / consent / masked ID) + "use it?" mapping questions + `bind` | ✅ Done, verified live on pminternship.mca.gov.in |
| **Role-based perception** (ARIA radio/checkbox/switch/listbox/textbox widgets) + role-generic execution + "can see but can't operate" report + per-fill trace | ✅ Done, verified in Chromium on a Google-Forms-shaped fixture |
| **Phase 3** — attachment library → auto-upload files into file fields | ⏳ Not started |
| Broaden Textract fallback recognizer (`extract.py`) | ⏳ Optional (VLM is now primary) |

**Tests:** backend 277 passed (+11 browser integration) · extension 107 passed · contracts no drift.

---

## 2. Architecture

_Full diagrams and the step-by-step form-filling journey mapped to components: **[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)**._

Deliberately **not** a monolithic LLM agent driving the browser. It is a
**deterministic skeleton with the LLM injected at bounded, verified points**.

### The LLM
- **Provider:** AWS Bedrock (Converse API), SSO profile `dev`, region `ap-south-1`.
- **Model:** Claude Sonnet 4 — `apac.anthropic.claude-sonnet-4-20250514-v1:0`
  (the code default; override with `BEDROCK_MODEL_ID`). It is **multimodal**,
  which the document extractor relies on. Upgraded from 3.7 Sonnet on 2026-09-10
  after a live pgportal smoke (11 fields, cascade selected, 2 model calls).
- **Why not newer?** The `dev` account is an AWS *channel program* (reseller)
  account; Bedrock rejects Sonnet 4.5/4.6/5, Haiku 4.5, Opus and Fable with
  "Access to this model is not available for channel program accounts". Unlocking
  them goes through the AWS distributor, not code. Once granted, switch by
  setting `BEDROCK_MODEL_ID` (e.g. `global.anthropic.claude-sonnet-5`) — the
  Converse API and document/image content blocks are unchanged. List what the
  account can see with `python -m agent_backend.model_gateway.list_models`.
- **Used only at these bounded points** — everything else is deterministic code,
  and each point degrades gracefully if Bedrock is unavailable:
  1. **Field → fact mapping** (when deterministic signals don't resolve a field)
  2. **Value derivation** (computing values from documents)
  3. **Chat interpretation** (free-text request + live form state → a small plan)
  4. **Document extraction** (VLM reads the PDF/image directly)
  5. **Repair** (corrected value for a field the site flagged)

### Three layers

**Chrome MV3 extension** (`apps/extension`)
- **Side panel** — the product surface: Upload document → editable profile → Fill;
  chat transcript + composer; conflict/answer prompts.
- **Background service worker** — owns the tab session and the deterministic
  guards (origin, replay/idempotency, submission lock); drives observe/execute
  against the page; holds the backend WebSocket; **persists the session to
  `chrome.storage.session` and reconnects** after Chrome recycles the worker.
- **Content script** — perceives the DOM/accessibility tree into a typed
  observation (incl. `maxlength`, `autocomplete`, and each field's **purpose**:
  credential / captcha / consent / standard, judged from the control's own
  text), executes one validated action, verifies against a fresh observation.
  Never captures credential values; a masked identifier (Aadhaar as a
  password-type input) is fillable but its value never leaves the page.

**Local backend** (`services/agent_backend`, FastAPI on `127.0.0.1:8000`)
- **WebSocket transport** bridges to the extension.
- **Driver** — `run_fill` (full fill) and `apply_edits` (targeted one-field edit).
- **Mapper** — exact name → autocomplete grounding → episodic memory → Bedrock →
  derivation, with a sensitivity/taint gate.
- **Policy gate** — every action re-validated (origin, no credentials, no submit
  without a human approval token, option membership). *Model output grants nothing.*
- **Independent verifier** + bounded **recovery** ladder.
- **Chat interpreter + snapshot** — the reasoning layer over live form state.
- **Document extraction** — VLM primary, Textract fallback; **profile merge**
  (dedup / add / conflict).
- **Auto-repair** — after a fill/edit, fix fields the site flags.
- **Persistence** — SQLite (runs, events, actions, idempotency, approvals, memory),
  file-backed by default at `./_agent_state/agent_backend.db`.

**Contracts** (`packages/contracts`) — Pydantic is the source of truth → JSON
Schema → generated TS/Zod; validated at the trust boundary. `check_drift.sh` gates CI.

### Two flows
- **Fill:** panel → attach tab + create run + open WS → `run_fill` drives
  observe→map→gate→act→verify→recover → auto-repair → report state + questions.
- **Chat edit:** panel text → backend: observe → snapshot → LLM plan → enforce a
  stated char limit → `apply_edits` (only the affected field) → auto-repair →
  honest reply + state (offers choices on a mismatch instead of a false "done").

**Invariants throughout:** never auto-submits; credentials never leave the page;
human-in-the-loop for questions/CAPTCHA/submission; every browser action passes
the policy gate and independent verification.

---

## 3. Profile creation (the document flow)

1. **📄 Upload a document** (before any fill). The VLM reads it layout-agnostically —
   tables, handwriting, abbreviations — resolves ambiguity (person vs guardian
   name, primary address), normalizes dates, infers obvious related fields
   (country from nationality), and targets the current form's fields when known.
   Proven on the messy intake fixture: **15 clean fields vs 2 noise facts** from Textract.
2. The result is **merged** into the profile: new field → added; same value →
   deduped; **different value → conflict** shown as "which value?" chips.
   Uploading with an existing profile asks **"Add to current, or start new?"**
3. The profile textarea is the **editable working copy** — edit values, add
   fields the form needs. "Clear profile" resets it.
4. **Fill this form.** Then chat to correct ("set state to Karnataka",
   "shorten the address to under 50 characters", "change it back").

---

## 4. Commit history (this body of work, oldest → newest)

| Commit | What |
|---|---|
| `4697500` | Failure-aware recovery: taxonomy + emit/act, plus dropdown fixes |
| `8183078` | Cross-run episodic mapping memory |
| `5089877` | Structural grounding via `autocomplete` + constrained-routing mapping |
| `50c1aaf` | Taint-aware mapping: sensitive values need a trusted binding |
| `1dc3cf3` | Durable-by-default DB so memory survives restarts |
| `a345e1b` | Phase 1: chat side panel with answer-and-resume + corrections |
| `28da5f4` | Reasoning chat 1: live form-state perception + reporting |
| `c1726ae` | Reasoning chat 2: LLM interpreter that understands the request |
| `6f174a1` | Fix chat session dropping after one turn (MV3 worker recycle) |
| `0280bb0` | Reasoning chat 3: targeted edits, undo history, cleaner replies |
| `a8b518a` | Fix edits not applying (stale sequence), honesty, tidy dropdowns |
| `ea2b7f2` | Phase 2: attach a document in chat → reviewable extracted facts |
| `b0eaf4b` | Phase 2: build the profile from the document (no fixed default) |
| `65903a7` | Panel: lead with Upload → editable profile → Fill |
| `9317056` | Multi-document profile: merge, dedup, conflicts, new-vs-add |
| `5704652` | VLM document extraction over Textract |
| `c693f7a` | Close the loop: length enforcement + auto-repair of flagged fields |

Pushed through `a8b518a`; the last six are local.

---

## 5. Notable bugs found & fixed (live testing)

- **Select2 cascade** (pgportal state→district) only fires on jQuery-triggered
  change; drive the widget UI (`mousedown` to open, `mousedown`+`mouseup` on the
  result), and collapse it afterwards.
- **Case-insensitive name matching** (`District` vs `district`) and **option
  label↔code satisfaction** (`476` ≡ `Thane`) so verification doesn't reject
  correct fills.
- **MV3 worker recycle** dropped the chat session → persist to
  `storage.session` and reconnect on demand.
- **Stale sequence numbers**: chat edits reused the session without
  re-attaching, so the replay guard rejected every edit action as stale (and
  `run_fill` burned its step budget climbing past it). Reset the counter per
  fill/edit.
- **False "done"** on a value that matched no option (typo "fmale" → `F`):
  report unresolved values honestly with the field's choices.
- **Length limits**: models miscount characters → clamp deterministically.
- **pminternship e-KYC (Aadhaar / consent / captcha)** — five generic causes:
  a masked Aadhaar box (`type=password`) was treated as a login credential, so
  the fill was abandoned and the field marked "complete on the page"; a text
  captcha wasn't recognised; a consent checkbox was asked as free text; "aid
  is aadharid" was read as a VALUE (and the failed edit still corrupted the
  profile); the mapper never proposed `aID` as a candidate. Fix: **field
  purpose** in perception (mirrored in the backend for defence in depth at the
  gate), captcha blocks submit only, consents need an explicit "Tick it", the
  mapper turns an uncertain candidate into "you have aID — use it?", a new
  `bind` op / `FA_BIND` for "this fact belongs in that field" (remembered for
  the site), one-off `set_field` for page-only fields, and `applied` only
  reports facts that actually landed.
- **Google Forms: choice questions silently skipped, "random" order.** Its
  radios, checkboxes and dropdowns are `div`s with ARIA roles; perception only
  knew native inputs and a closed list of widgets — an allowlist that always
  has a next hole. Replaced with **role-based perception** (`perception/aria.ts`:
  any `role=radiogroup/radio/checkbox/switch/listbox/option/textbox` or
  contenteditable is a field, state from `aria-checked/selected/required`),
  **role-generic execution** (click / open-and-pick / type), fields ordered
  by DOM position so the fill runs top to bottom, an `unrecognized_controls`
  report ("I can see X but can't operate it") instead of silent skipping, and
  a value-free **`fill_trace` / `edit_trace`** event per operation so the run
  log answers "why didn't it fill X?". Residual: sites with no roles at all →
  the planned visual (screenshot→VLM) fallback.
- **"COMPLETED" with nothing filled (2026-09-16).** The backend had been
  started without `AWS_PROFILE`/`AWS_REGION`; Bedrock raised
  `ExpiredTokenException`, the mapper abstained silently and the run reported
  success. Now: the mapper records `model_unavailable`, the driver reports
  NEEDS_USER with the reason, the panel says so plainly, `/health` +
  a startup credential probe catch it before a fill, and the fill trace
  records `facts_count`. Radio groups also carry per-option labels so
  "Female" matches a radio coded "F".
- **Vipassana application (schedule.vridhamma.org, 2026-09-28): 2 → 25 fields.**
  A Drupal webform wizard whose "Next" does a full page load. Five generic
  causes: (1) the observation right after "Next" was still the old page, so
  the driver concluded "did not advance" — it now waits (bounded) for the new
  page; (2) the content script injected at attach died with the old document
  — the extension re-injects it (same origin only); (3) a 45-field page made
  the mapping answer exceed the 1500-token output cap, truncating the JSON and
  losing every mapping — mapping is batched (20 fields/call) and the cap is
  4096; (4) the honeypot check used viewport coordinates, so every field the
  page had scrolled past vanished — it uses document coordinates now; (5) the
  final "all fields filled" message overwrote the real stop reason, and stale
  questions for already-filled fields were reported. Also: Yes/No questions
  take plain answers ("None" → No), and the VLM extractor keeps every stated
  fact, not only the 20 standard keys.
- **policybazaar term-life quote (2026-09-29).** The live site is behind
  Akamai bot protection (403 for automated browsers), so it was reproduced as
  a fixture (`apps/fixtures/masked-form`). Three generic bugs: (1) a date box
  of `type="tel"` (an input mask) was adapted as a PHONE number — dates now
  take precedence, and numeric-keypad date boxes get DD-MM-YYYY / DDMMYYYY
  first; (2) a site reformatting what was typed (mask separators, upper-case
  name) was treated as a mismatch — same letters/digits in order now counts as
  filled (`site_normalized`); (3) after the user answered a question the flow
  ended — answers, bindings and chat edits now resume the fill and press Next
  (`_continue_flow`), and the driver no longer presses Next while a required
  field on the page is still empty (it stops, asks, and continues after).
- **Fill order + live progress (2026-09-29).** Fields were filled in the
  mapper's resolution order (exact matches, then memory, then model), so the
  cursor jumped around; library widgets (Select2) were also appended after the
  DOM-order sort. Now the driver walks the page top to bottom and perception
  sorts after widget merges. The driver streams progress (`fill_progress`:
  reading / matching / planned / filling "label" n of N / retrying /
  next_page / checking) to a live card in the side panel (spinner, bar,
  current field, recently filled fields, elapsed time); each field is scrolled
  into view and briefly outlined on the page as it is filled. The final screen
  shows one card per field, human wording, Tick it / Leave it for consents,
  and pickers already showing the value count as done.
- **Reliability + filling like a person (2026-09-29).** 6 of 10 real fills on
  the Vipassana form had failed with no trace. Cause: when the backend asked
  the extension to read a page that was mid-reload, the extension either
  replied with the PREVIOUS page's observation or never replied (the backend
  timed out). Now it always replies, with an error instead of stale data,
  waits for a loading tab, and the backend retries briefly
  (`ResilientTransport`, `PageUnavailable`). Verified: 3/3 real side-panel →
  WebSocket runs, no errors. Values are now fitted to the field BEFORE
  typing (`adapt.py`: national phone number when the field says 10 digits,
  has a pattern, or sits next to a country-code picker; dates in the field's
  stated order; long addresses abbreviated then trimmed), constraints are read
  from validation libraries too (ASP.NET `data-val-*`, Parsley, jQuery
  Validate, Angular); a rejected value is re-tried in the next format, then
  repaired by the model from the site's own error message (accepted only as a
  reshape of the same fact), and only then asked — quoting the site's message.
  Optional fields are never asked about; they are listed as left blank.
  pgportal: 11/11 COMPLETED, no questions. Vipassana: 25 filled, 6 questions,
  all genuine profile gaps.
- **Latency (2026-09-29): Vipassana fill 166 s → 26 s, 20 → 5 model calls.**
  Browser work was ~5 s; the rest was the model. Causes: every re-map of a
  re-rendering page re-asked the model the same questions (5 identical
  20k-token calls); 250-option country/phone-code lists bloated prompts to
  21k tokens; long "reason" strings made answers slow to write; derivation
  repeated per re-map. Fixes: per-field answer cache in the mapper (keyed by
  field identity + profile; "no match" cached too), derivation cache, batches
  of 10 run in parallel, option lists > 40 left out of the prompt (values still
  matched deterministically), terse output.
- **A modal that holds the form** (the site's "Register" dialog) was dismissed
  as an obstacle → `DialogInfo.contains_form`; the driver never dismisses it.
- **`aria-describedby` is a description, not an error** (react-select points it
  at the placeholder) — only counted when the control is `aria-invalid`.

---

## 6. Running it

```bash
# 1. AWS (interactive)
aws sso login --profile dev

# 2. Backend (from the repo root, so durable memory lands in ./_agent_state).
#    AWS_PROFILE/AWS_REGION are REQUIRED in the server's environment — without
#    them boto3 falls back to the default profile and the model is unreachable.
#    The server prints "model credentials: ok" (or a WARNING) at startup, and
#    GET /health reports model_ready; the panel warns before a fill if not.
export PATH="$HOME/.local/bin:$PATH"
export AWS_PROFILE=dev AWS_REGION=ap-south-1
# (optional) export BEDROCK_MODEL_ID=...   # default is Claude Sonnet 4 (apac profile)
uv run --package agent-backend --extra server python -m agent_backend.api.server
# → serves http://127.0.0.1:8000 ; prints "model provider: ModelAssistedMapper (<model id>)"

# 3. Extension — `build:dev` widens dist/manifest.json to <all_urls> so you can
#    try it on any site (the committed manifest stays localhost-only)
pnpm --filter @form-agent/extension build:dev
# chrome://extensions → Developer mode → Load unpacked → apps/extension/dist (↻ reload after rebuilds)
```

Tests: `uv run pytest services/agent_backend/tests/ -q` ·
`cd apps/extension && npx tsc --noEmit && npx vitest run` ·
`bash packages/contracts/check_drift.sh`.

Environment notes: managed work Mac behind a corporate TLS proxy (`NODE_EXTRA_CA_CERTS`
for Node downloads; `AWS_CA_BUNDLE` if boto3 hits SSL errors); `pnpm` at `~/.local/bin`.

---

## 7. Known limits / next steps

- **Perception is role-based, not visual.** A site whose widgets expose no
  ARIA role at all (bare click-driven divs) is still invisible; those are the
  case for the visual fallback (screenshot → VLM grounding) reserved in the plan.
- **Auto-repair needs a detectable error** — one the page surfaces (error text,
  `aria-invalid`, `maxlength`). Rules enforced only on submit are invisible to it;
  a stated instruction ("under 50 characters") covers those.
- **`chrome.storage.session` clears on browser restart** — click Fill once to
  start a fresh session.
- **Phase 3 (next):** a persistent attachment library (resume, photo, ID) with an
  `AttachmentResolver` that matches a form's file field (accept type + label) and
  auto-uploads through the existing `UploadFileRef` path; confirm before uploading
  sensitive docs or on an ambiguous match.
- Optional: broaden the Textract fallback recognizer; visual/positional grounding
  fusion if a site's accessibility tree ever proves insufficient.
