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
from ..snapshot import build_snapshot, summarize
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


def _reperceive(st, transport, run_id) -> dict | None:
    """Snapshot the form as it now stands and summarize the delta since the last
    turn. Best-effort; never fails the caller."""
    try:
        snapshot = build_snapshot(transport.observe())
        state = summarize(snapshot, st.form_state.get(run_id))
        st.form_state[run_id] = snapshot
        return state
    except Exception:  # noqa: BLE001 — perception is best-effort
        return None


def _fill_payload(result, state) -> dict:
    """The result fields shared by a plain fill and a chat turn."""
    return {
        "outcome": result.outcome.value,
        "filled": result.filled_fields,
        "questions": [
            {
                "field_id": q.field_id,
                "kind": q.kind.value,
                "prompt": q.prompt,
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


def chat(st, session, loop, text: str, fact_items: list[dict], send, history=None) -> None:
    """A reasoning chat turn: perceive the live form, interpret the message into
    a plan (LLM with a deterministic fallback), apply fact updates through the
    gated pipeline, auto-repair, and report the new state honestly. The plan can
    only set facts / refill / explain."""

    def work(transport):
        snapshot = build_snapshot(transport.observe())
        plan = interpret_or_fallback(_gateway(st), text, snapshot, fact_items, history or [])
        updates = plan.fact_updates()
        # A stated character limit ("under 50 characters") is enforced
        # deterministically — models miscount characters.
        limit = parse_char_limit(text)
        if limit is not None:
            updates = [{"key": u["key"], "value": clamp_length(u["value"], limit)} for u in updates]
        payload = {"type": "chat_result", "reply": plan.reply, "applied": updates}

        if updates:
            # Targeted edit: only the affected field(s), not a whole-form re-fill.
            merged = upsert_facts(fact_items, updates)
            edit = apply_edits(
                transport, facts_from_payload(merged), {u["key"] for u in updates}, mapper=st.mapper
            )
            repaired, _ = auto_repair(st, transport, session, merged)
            reply, questions = _edit_reply(plan.reply, edit, repaired)
            payload.update(
                {
                    "reply": reply,
                    "outcome": "EDITED",
                    "filled": edit.filled_fields,
                    "questions": questions,
                    "validation_issues": [],
                    "state": _reperceive(st, transport, session.run_id),
                    "detail": None,
                }
            )
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
