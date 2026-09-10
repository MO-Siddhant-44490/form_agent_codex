"""The operations the extension can request over its WebSocket — fill, chat
turn, document parse — orchestrated over the driver/mapper/transport. Each runs
in a worker thread (the driver is synchronous) and streams one result envelope
back to the panel. Never submits a form."""

import asyncio
import base64
import threading
from collections.abc import Callable

from ..chat_agent import (
    clamp_length,
    interpret_or_fallback,
    parse_char_limit,
    repair_values,
)
from ..document_intelligence.extract import extract_facts
from ..document_intelligence.parser_factory import build_parser_from_env
from ..document_intelligence.store import DocumentRejected
from ..document_intelligence.vlm_extract import supports_vlm, vlm_extract
from ..driver import EditResult, apply_edits, run_fill
from ..model_gateway.base import ModelUnavailable
from ..profile import facts_from_payload, merge_document, upsert_facts
from ..snapshot import build_snapshot, is_filled, summarize, unrecognized
from .transport_ws import WebSocketBrowserTransport

# -- plumbing ---------------------------------------------------------------


def _run_in_worker(st, session, loop, send, work: Callable, *, event: str) -> None:
    """Run `work(transport) -> payload` on a worker thread and send the payload
    to the panel. A raised exception becomes an error envelope so a failure never
    strands the panel."""

    def worker():
        transport = WebSocketBrowserTransport(session, loop)
        try:
            payload = work(transport)
        except DocumentRejected as error:
            payload = {"type": f"{event}_error", "error": str(error)}
        except Exception as error:  # noqa: BLE001 — surface, don't crash the socket
            payload = {"type": f"{event}_error", "error": str(error)}
        st.repo.append_event(
            session.run_id, f"{event}_completed", {"outcome": payload.get("outcome")}
        )
        asyncio.run_coroutine_threadsafe(send(payload), loop)

    threading.Thread(target=worker, daemon=True).start()


def _gateway(st):
    return getattr(st.mapper, "_gateway", None)


def st_fields(state: dict | None) -> list[dict]:
    """Every field the state summary knows a label for."""
    if not state:
        return []
    return [
        *state.get("empty_required", []),
        *state.get("new_fields", []),
        *state.get("errors", []),
    ]


def _trace(st, run_id: str, kind: str, observation, **parts) -> None:
    """Persist what a fill/edit SAW and DID, so a 'why didn't it fill X?' is
    answerable from the run log (GET /runs/{id}/events) instead of guesswork.
    Value-free: field identities, purposes, outcomes — never the values."""
    fields = (
        [
            {
                "field_id": f.field_id,
                "type": f.input_type,
                "label": f.label or f.accessible_name,
                "purpose": f.purpose.value,
                "required": f.required,
                "filled": is_filled(f),
            }
            for f in observation.fields
        ]
        if observation is not None
        else []
    )
    st.repo.append_event(
        run_id,
        kind,
        {
            "fields": fields,
            "unrecognized": [u.model_dump() for u in observation.unrecognized_controls]
            if observation is not None
            else [],
            **parts,
        },
    )


def _drive_trace(result) -> dict:
    return {
        "outcome": result.outcome.value,
        "detail": result.detail,
        "filled": result.filled_fields,
        "questions": [
            {"field_id": q.field_id, "kind": q.kind.value, "fact_keys": list(q.fact_keys)}
            for q in result.questions
        ],
        "verifications": [
            {
                "status": v.status.value,
                "failure_class": v.failure_class.value if v.failure_class else None,
                "notes": v.evidence.notes,
            }
            for v in result.verifications
        ],
        "recovery": [
            {"field_id": d.field_id, "strategy": d.strategy.value}
            for d in result.recovery_decisions
        ],
        "policy_blocks": [
            {"rule": d.rule.value if d.rule else None, "detail": d.detail}
            for d in result.policy_decisions
            if d.decision.value == "BLOCK"
        ],
        "model_calls": len(result.model_calls),
        "steps": result.steps_used,
    }


def _observe_quietly(transport):
    try:
        return transport.observe()
    except Exception:  # noqa: BLE001 — tracing must never fail an operation
        return None


def _reperceive(st, transport, run_id) -> dict | None:
    """Snapshot the form as it now stands and summarize the delta since the last
    turn. Best-effort; never fails the caller."""
    try:
        observation = transport.observe()
        snapshot = build_snapshot(observation)
        state = summarize(snapshot, st.form_state.get(run_id), unrecognized(observation))
        st.form_state[run_id] = snapshot
        return state
    except Exception:  # noqa: BLE001 — perception is best-effort
        return None


def _fill_payload(result, state) -> dict:
    """The result fields shared by a plain fill and a chat turn."""
    labels = {s["field_id"]: s["label"] for s in st_fields(state)}
    return {
        "outcome": result.outcome.value,
        "filled": result.filled_fields,
        "questions": [
            {
                "field_id": q.field_id,
                "kind": q.kind.value,
                "prompt": q.prompt,
                "label": labels.get(q.field_id, q.field_id),
                # fact_keys routes an answer to the right fact; options render choices.
                "fact_keys": list(q.fact_keys),
                "options": list(q.options) if q.options else None,
            }
            for q in result.questions
        ],
        "validation_issues": [
            {"field_id": i.field_id, "kind": i.kind.value, "label": i.label, "detail": i.detail}
            for i in result.validation.issues
        ],
        "state": state,
        "detail": result.detail,
    }


# -- auto-repair ------------------------------------------------------------


def auto_repair(st, transport, session, fact_items: list[dict], rounds: int = 2):
    """Close the loop after a fill/edit: find fields the site is now flagging
    (a validation error/warning) and try to fix them — shorten an over-long
    value, reformat — instead of leaving them for the user. Bounded; each fix
    goes through the same gated apply_edits. Returns (fixed_keys, fact_items)."""
    fixed: list[str] = []
    for _ in range(rounds):
        try:
            observation = transport.observe()
        except Exception:  # noqa: BLE001
            break
        flagged = [s for s in build_snapshot(observation) if s.get("error") and s.get("value")]
        if not flagged:
            break
        facts_by_key = {f.key: f for f in facts_from_payload(fact_items)}
        mapping = st.mapper.map(observation, facts_by_key)
        issues = []
        for s in flagged:
            assignment = mapping.assignments.get(s["field_id"])
            if assignment is not None:
                issues.append(
                    {
                        "key": assignment.fact.key,
                        "label": s["label"],
                        "value": s["value"],
                        "error": s["error"],
                        "max_length": s.get("max_length"),
                    }
                )
        corrections = repair_values(_gateway(st), issues)
        for issue in issues:
            cap = issue.get("max_length")
            if issue["key"] in corrections and cap:
                corrections[issue["key"]] = clamp_length(corrections[issue["key"]], cap)
        corrections = {k: v for k, v in corrections.items() if v}
        if not corrections:
            break
        fact_items = upsert_facts(
            fact_items, ({"key": k, "value": v} for k, v in corrections.items())
        )
        apply_edits(transport, facts_from_payload(fact_items), set(corrections), mapper=st.mapper)
        fixed.extend(corrections)
    return fixed, fact_items


# -- operations -------------------------------------------------------------


def start_fill(st, session, loop, fact_items: list[dict], send) -> None:
    """Fill the connected tab from the profile, auto-repair anything the site
    flags, and report filled fields, questions, and the live form state."""

    def work(transport):
        result = run_fill(
            transport, facts_from_payload(fact_items), mapper=st.mapper, memory=st.memory
        )
        auto_repair(st, transport, session, fact_items)
        observation = _observe_quietly(transport)
        _trace(st, session.run_id, "fill_trace", observation, **_drive_trace(result))
        return {
            "type": "fill_result",
            **_fill_payload(result, _reperceive(st, transport, session.run_id)),
        }

    _run_in_worker(st, session, loop, send, work, event="fill")


def _edit_reply(plan_reply: str, edit: EditResult, repaired: list[str]) -> tuple[str, list[dict]]:
    """The reply to show for a targeted edit — the ACTUAL outcome, not the
    interpreter's optimistic reply: a value that matched no field/option is
    reported with the choices, and any auto-repair is mentioned."""
    reply = plan_reply
    questions: list[dict] = []
    if edit.unresolved or edit.failed_fields:
        said = []
        for u in edit.unresolved:
            choices = f" Choices: {', '.join(u['options'])}." if u.get("options") else ""
            said.append(f"I couldn't set {u['field_label']} to {u['value']!r}.{choices}")
            questions.append(
                {
                    "field_id": u["field_label"],
                    "kind": "ambiguous_mapping",
                    "prompt": f"Which value for {u['field_label']}?",
                    "fact_keys": [u["key"]],
                    "options": u.get("options"),
                }
            )
        said.extend(f"I couldn't fill {fid}." for fid in edit.failed_fields)
        reply = " ".join(said) + " What should I use?"
    if repaired:
        reply += f" I also adjusted {', '.join(sorted(set(repaired)))} so the form accepts it."
    return reply, questions


def _resolve_field(snapshot: list[dict], ref: str) -> str | None:
    """The field_id a user/model reference names — by id, exact label, then a
    case-insensitive containment either way ("aadhaar" ~ "Aadhaar Number/Virtual ID *")."""
    low = ref.strip().lower()
    for s in snapshot:
        if s["field_id"] == ref or s["label"].strip().lower() == low:
            return s["field_id"]
    for s in snapshot:
        lab = s["label"].strip().lower()
        if low in lab or lab in low:
            return s["field_id"]
    return None


def _edit(st, transport, session, plan_reply: str, fact_items, keys, bindings=None) -> dict:
    """Apply a targeted edit (facts `keys`, plus explicit field<-fact `bindings`),
    auto-repair, and build the honest result envelope."""
    edit = apply_edits(
        transport,
        facts_from_payload(fact_items),
        keys,
        mapper=st.mapper,
        bindings=bindings,
        memory=st.memory,
    )
    repaired, _ = auto_repair(st, transport, session, fact_items)
    reply, questions = _edit_reply(plan_reply, edit, repaired)
    _trace(
        st,
        session.run_id,
        "edit_trace",
        _observe_quietly(transport),
        keys=sorted(keys),
        bindings=bindings or {},
        filled=edit.filled_fields,
        failed=edit.failed_fields,
        unresolved=[u["key"] for u in edit.unresolved],
        repaired=repaired,
    )
    return {
        "reply": reply,
        "outcome": "EDITED",
        "filled": edit.filled_fields,
        "unresolved": [u["key"] for u in edit.unresolved],
        "questions": questions,
        "validation_issues": [],
        "state": _reperceive(st, transport, session.run_id),
        "detail": None,
    }


def chat(st, session, loop, text: str, fact_items: list[dict], send, history=None) -> None:
    """A reasoning chat turn: perceive the live form, interpret the message into
    a plan (LLM with a deterministic fallback), apply fact updates through the
    gated pipeline, auto-repair, and report the new state honestly. The plan can
    only set facts / bind a fact to a field / refill / explain."""

    def work(transport):
        snapshot = build_snapshot(transport.observe())
        plan = interpret_or_fallback(_gateway(st), text, snapshot, fact_items, history or [])
        updates = plan.fact_updates()
        # A stated character limit ("under 50 characters") is enforced
        # deterministically — models miscount characters.
        limit = parse_char_limit(text)
        if limit is not None:
            updates = [{"key": u["key"], "value": clamp_length(u["value"], limit)} for u in updates]
        bindings = {}
        for b in plan.bindings():
            fid = _resolve_field(snapshot, b["field"])
            if fid is not None and any(f.get("key") == b["key"] for f in fact_items):
                bindings[fid] = b["key"]
        payload = {"type": "chat_result", "reply": plan.reply, "applied": []}

        if updates or bindings:
            # Targeted edit: only the affected field(s), not a whole-form re-fill.
            merged = upsert_facts(fact_items, updates)
            keys = {u["key"] for u in updates} | set(bindings.values())
            payload.update(_edit(st, transport, session, plan.reply, merged, keys, bindings))
            # Only facts that actually landed (or are pure profile facts with no
            # field on this page) are reported as applied — an unresolved value
            # must not silently corrupt the profile.
            unresolved = set(payload["unresolved"])
            payload["applied"] = [u for u in updates if u["key"] not in unresolved]
        elif any(o.op == "refill" for o in plan.ops):
            result = run_fill(
                transport, facts_from_payload(fact_items), mapper=st.mapper, memory=st.memory
            )
            payload.update(_fill_payload(result, _reperceive(st, transport, session.run_id)))
        else:
            # Explain-only: report the current state without acting.
            payload["state"] = summarize(snapshot, st.form_state.get(session.run_id))
            st.form_state[session.run_id] = snapshot
        return payload

    _run_in_worker(st, session, loop, send, work, event="chat")


def bind(st, session, loop, field_id: str, key: str, fact_items: list[dict], send) -> None:
    """The user confirmed a proposed binding ("use aID for Aadhaar Number"):
    fill that field from that fact, deterministically — no model call — and
    remember the binding for this site."""

    def work(transport):
        snapshot = build_snapshot(transport.observe())
        fid = _resolve_field(snapshot, field_id)
        if fid is None or not any(f.get("key") == key for f in fact_items):
            return {
                "type": "chat_result",
                "reply": f"I can't find '{field_id}' on the page or '{key}' in your profile.",
                "applied": [],
                "state": summarize(snapshot, st.form_state.get(session.run_id)),
            }
        label = next(s["label"] for s in snapshot if s["field_id"] == fid)
        payload = {"type": "chat_result", "applied": []}
        payload.update(
            _edit(
                st, transport, session, f"Using {key} for {label}.", fact_items, {key}, {fid: key}
            )
        )
        return payload

    _run_in_worker(st, session, loop, send, work, event="chat")


TRANSIENT_PREFIX = "field:"


def set_field(st, session, loop, field_id: str, value: str, fact_items: list[dict], send) -> None:
    """The user answered for a field that has no profile fact — a consent box
    ("Tick it"), a form-specific required field. Apply the value to THAT field
    as a one-off, through the gated pipeline, without adding a page-specific
    key to the profile."""

    def work(transport):
        snapshot = build_snapshot(transport.observe())
        fid = _resolve_field(snapshot, field_id)
        if fid is None:
            return {
                "type": "chat_result",
                "reply": f"I can't find '{field_id}' on the page any more.",
                "applied": [],
                "state": summarize(snapshot, st.form_state.get(session.run_id)),
            }
        label = next(s["label"] for s in snapshot if s["field_id"] == fid)
        key = f"{TRANSIENT_PREFIX}{fid}"
        facts = [*fact_items, {"key": key, "value": value}]
        payload = {"type": "chat_result", "applied": []}
        payload.update(_edit(st, transport, session, f"Set {label}.", facts, {key}, {fid: key}))
        return payload

    _run_in_worker(st, session, loop, send, work, event="chat")


def _extract_profile(st, session, data: bytes, record):
    """Facts from a stored document: the reasoning VLM reads it directly
    (layout-agnostic, targeting the observed form's fields when known); the
    Textract + recognizer pipeline is the fallback for unsupported types or a
    model outage."""
    gateway = _gateway(st)
    target = [f["label"] for f in st.form_state.get(session.run_id, []) if f.get("label")]
    if supports_vlm(gateway):
        try:
            return vlm_extract(gateway, data, record.mime_type, target or None)
        except (ModelUnavailable, ValueError):
            pass
    parsed = build_parser_from_env().parse(
        record.document_id, st.documents.blob(record.document_id), record.mime_type
    )
    return list(extract_facts(parsed, keep_unknown=True).facts)


def parse_document(
    st, session, loop, filename, mime, content_b64, current_facts: list[dict], send
) -> None:
    """Store an uploaded document, extract a profile from it, and MERGE it into
    the current profile — returning merged fields plus any conflicts for the
    user to resolve. Values are the user's own data on their own machine."""

    def work(_transport):
        data = base64.b64decode(content_b64 or "")
        record = st.documents.upload(data, filename or "upload", mime or "application/octet-stream")
        st.repo.record_document(
            record.document_id,
            session.run_id,
            record.filename,
            record.mime_type,
            record.size_bytes,
            record.sha256,
            storage_key=f"docs/{record.document_id}",
        )
        extracted = _extract_profile(st, session, data, record)
        fields, conflicts = merge_document(current_facts or [], extracted)
        existing = {f.get("key") for f in (current_facts or [])}
        return {
            "type": "document_facts",
            "filename": record.filename,
            "fields": fields,
            "conflicts": conflicts,
            "added": [f["key"] for f in fields if f["key"] not in existing],
        }

    _run_in_worker(st, session, loop, send, work, event="document")
