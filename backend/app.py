from __future__ import annotations

import os
import json
import hashlib
import re
import threading
import time
import uuid
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import Any, Dict, Literal, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from backend import config
from backend.design import colors, engine, presentation, step_catalog
from backend.pdf import pdf_export
from backend.services import ai, chat_actions, image_model
from backend.sessions.sessions import SessionManager, SessionMiddleware, current_session, current_registry, current_owner, confirmed, document, restore
from backend.sessions.session_store import RETENTION_DAYS, EVENTS

ROOT = os.path.dirname(os.path.dirname(__file__))
FRONTEND_DIR = os.path.join(ROOT, "frontend")
PDF_PREVIEW_LOCK = threading.Lock()
PDF_PREVIEW_WORKER = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sisa-pdf-preview")
BODY_LIMIT = 65536
ACTION_DEADLINE_S = 100
AI_CALL_TIMEOUT_S = ai.CALL_TIMEOUT_S
SEARCH_BUDGET = {"max_checks": 50000, "timeout_s": 5}


def error(status, code, message, **details):
    return HTTPException(status_code=status, detail={"code": code, "message": message, **details})


def stale_error():
    return error(409, "STALE_REVISION", "Sesi atau revisi berubah; hasil lama dibatalkan.")


def engine_detail(exc):
    codes = {"INVALID_INPUT", "UNSUPPORTED_CONSTRUCTION", "UNCONFIRMED_INPUT", "AREA_LOWER_BOUND_FAIL", "NO_LAYOUT_WITHIN_BUDGET", "STALE_REVISION", "VALIDATION_FAILED", "CONSTRUCTION_UNREVIEWED", "INVALID_STATE", "PDF_CONTENT_TOO_WIDE", "PDF_CONTENT_TOO_TALL", "PDF_PATTERN_TOO_LARGE"}
    code = exc.code if exc.code in codes else "INVALID_INPUT"
    return {"code": code, "message": "Permintaan ditolak oleh pemeriksaan backend; periksa input, revisi dan status konstruksi."}




@dataclass(frozen=True)
class Snapshot:
    session: SessionManager
    session_id: str
    generation: int
    job_token: str
    ai_job_token: str
    signature: str
    data: Dict[str, Any]
    consent: Optional[Dict[str, Any]]


def capture(session):
    return Snapshot(session, session.session_id, session.generation_token, session.job_token, session.ai_job_token, session.signature(), deepcopy(session.data), deepcopy(session.consent))


def current_session_is_live(session):
    registry = current_registry()
    return session.alive and registry is not None and registry.is_current(current_owner(), session)


def check_token(session, token):
    if not current_session_is_live(session) or (token is not None and token != session.generation_token):
        raise stale_error()


def check_snapshot(snapshot, ai_job=False):
    session = snapshot.session
    if not current_session_is_live(session) or session.session_id != snapshot.session_id or session.generation_token != snapshot.generation or session.job_token != snapshot.job_token or session.signature() != snapshot.signature or (ai_job and session.ai_job_token != snapshot.ai_job_token):
        raise stale_error()


class StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class TokenPayload(StrictBase):
    expected_generation_token: Optional[int] = Field(default=None, gt=0)


class InputPayload(TokenPayload):
    data: Dict[str, Any]


class PlanPayload(TokenPayload):
    use_ai: bool = False
    ai_consent: bool = False


class AcceptPayload(TokenPayload):
    accepted: bool
    additional_seams: int = Field(ge=1, le=1)
    candidate_signature: Optional[str] = Field(default=None, min_length=64, max_length=64)


class AiConsentPayload(TokenPayload):
    ai_consent: bool = False


class AiRequestPayload(AiConsentPayload):
    text: str = Field(min_length=0, max_length=2000)


class ChatPayload(AiRequestPayload):
    replan: bool = False


class ImagePayload(AiConsentPayload):
    regenerate: bool = False


class ImageModelPayload(TokenPayload):
    image_id: str = Field(min_length=36, max_length=36)


class ProjectSavePayload(TokenPayload):
    name: str = Field(min_length=1, max_length=40)


class ProjectOpenPayload(TokenPayload):
    project_id: str = Field(min_length=36, max_length=36)


class TelemetryConsentPayload(StrictBase):
    enabled: bool


class TelemetryEventPayload(StrictBase):
    event: Literal["start", "story_sent", "design_ready", "download", "error"]


def authority(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or parsed.username is not None or parsed.password is not None or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Invalid authority")
    hostname = (parsed.hostname or "").lower()
    if parsed.hostname is None or hostname not in ("localhost", "127.0.0.1", "testserver"):
        raise ValueError("Invalid authority")
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if not 1 <= port <= 65535:
        raise ValueError("Invalid port")
    return parsed.scheme, hostname, port


class SecurityMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request = Request(scope)

        async def reject(status, code, message):
            await JSONResponse({"detail": {"code": code, "message": message}}, status_code=status, headers={"Cache-Control": "no-store"})(scope, receive, send)

        try:
            host = authority(f'{scope.get("scheme", "http")}://{request.headers.get("host", "")}')
        except ValueError:
            return await reject(400, "UNTRUSTED_HOST", "Host lokal tidak valid.")
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return await reject(403, "UNTRUSTED_ORIGIN", "Permintaan lintas situs ditolak.")
        origin = request.headers.get("origin")
        if origin is not None:
            try:
                same = authority(origin) == host
            except ValueError:
                same = False
            if not same:
                return await reject(403, "UNTRUSTED_ORIGIN", "Origin harus sama dengan layanan lokal.")
        length = request.headers.get("content-length")
        if length is not None:
            if not re.fullmatch(r"[0-9]+", length):
                return await reject(400, "INVALID_INPUT", "Content-Length tidak valid.")
            if int(length) > BODY_LIMIT:
                return await reject(413, "PAYLOAD_TOO_LARGE", "Batas body 65536 byte.")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > BODY_LIMIT:
                return await reject(413, "PAYLOAD_TOO_LARGE", "Batas body 65536 byte.")
            if not message.get("more_body", False):
                break
        consumed = False
        started = False

        async def replay():
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        async def safe_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                if scope["path"].startswith("/api/"):
                    headers = [(key, value) for key, value in message.get("headers", []) if key.lower() != b"cache-control"]
                    message = {**message, "headers": headers + [(b"cache-control", b"no-store")]}
            await send(message)

        try:
            await self.app(scope, replay, safe_send)
        except Exception:
            if started:
                raise
            await reject(500, "INTERNAL_ERROR", "Layanan gagal memproses permintaan; coba lagi.")


@asynccontextmanager
async def lifespan(app):
    config.load_local_config()
    yield


app = FastAPI(title="SISA API", version="0.5.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware)
app.add_middleware(SecurityMiddleware)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.exception_handler(RequestValidationError)
async def invalid_payload(request: Request, exc: RequestValidationError):
    if request.url.path == "/api/input":
        with current_session().lock:
            if not current_session_is_live(current_session()):
                return JSONResponse(status_code=409, content={"detail": {"code": "STALE_REVISION", "message": "Sesi berubah; permintaan lama dibatalkan."}})
            current_session().invalidate()
    fields = [{"loc": list(item["loc"]), "type": item["type"]} for item in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": {"code": "INVALID_INPUT", "message": "Field atau tipe input tidak valid.", "fields": fields}})


@app.get("/")
def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/app.js")
def serve_app_js():
    return FileResponse(os.path.join(FRONTEND_DIR, "src", "app.js"), media_type="text/javascript")


@app.get("/colors.js")
def serve_colors_js():
    return FileResponse(os.path.join(FRONTEND_DIR, "src", "config", "colors.js"), media_type="text/javascript")


@app.get("/style.css")
def serve_style_css():
    # Compatibility for old local bookmarks; the current UI loads four layers.
    return FileResponse(os.path.join(FRONTEND_DIR, "styles", "base.css"), media_type="text/css")


@app.get("/api/state")
def get_state():
    with current_session().lock:
        check_token(current_session(), None)
        return current_session().payload()


@app.get("/api/config")
def get_config():
    return {
        "ai_available": ai.available(),
        "tolerance": {"printed_square_mm": engine.CONFIG["printed_square_mm"], "printed_panel_mm": engine.CONFIG["printed_panel_mm"], "sewn_exterior_mm": engine.CONFIG["sewn_exterior_mm"]},
        "color_palette": deepcopy(colors.PALETTE),
        "ai_primary": True,
        "ai_image_available": ai.image_available(),
        "image_model_available": image_model.available(),
        "construction_status": engine.t.REVIEW_STATUS,
        "template_id": engine.t.TEMPLATE_ID,
        "template_version": engine.t.TEMPLATE_VERSION,
        "config_version": engine.CONFIG["version"],
        "max_materials": 4,
        "max_defects_per_material": 2,
        "w_range_mm": list(engine.t.W_RANGE),
        "h_range_mm": list(engine.t.H_RANGE),
        "part_names": presentation.PART_NAMES,
        "check_labels": presentation.CHECK_LABELS,
        "allowances": {"seam_mm": engine.t.S, "fold1_mm": engine.t.HEM_FOLD_1_MM, "fold2_mm": engine.t.HEM_FOLD_2_MM},
    }


@app.post("/api/reset")
def reset_state(payload: Optional[TokenPayload] = None):
    session = current_session()
    with session.lock:
        check_token(session, payload.expected_generation_token if payload else None)
        replacement = SessionManager()
        replacement.generation_token = session.generation_token + 1
        replacement.chat_calls = session.chat_calls
        replacement.image_calls = session.image_calls
        replacement.telemetry_enabled = session.telemetry_enabled
        replacement.anonymous_id = session.anonymous_id
        current_registry().replace(current_owner(), session, replacement)
        return replacement.payload()


@app.post("/api/input")
@app.put("/api/input")
def update_input(payload: InputPayload):
    with current_session().lock:
        session = current_session()
        check_token(session, payload.expected_generation_token)
        try:
            validated = engine.validate_input(payload.data)
        except engine.EngineError as exc:
            session.invalidate()
            raise HTTPException(status_code=422, detail=engine_detail(exc)) from exc
        old_req = session.data["request"]
        new_req = validated["request"]
        same_request = {k: v for k, v in old_req.items() if k != "confirmed"} == {k: v for k, v in new_req.items() if k != "confirmed"}
        new_req["confirmed"] = same_request and old_req["confirmed"] and new_req["confirmed"]
        old_materials = {m["id"]: m for m in session.data["materials"]}
        for material in validated["materials"]:
            old = old_materials.get(material["id"])
            keys = set(material) - {"confirmed", "available"}
            unchanged = old is not None and all(material[key] == old[key] for key in keys)
            material["confirmed"] = unchanged and old["confirmed"] and material["confirmed"]
        if validated != session.data:
            session.invalidate()
            session.data = deepcopy(validated)
        return session.payload()


@app.post("/api/cancel")
def cancel(payload: TokenPayload):
    with current_session().lock:
        check_token(current_session(), payload.expected_generation_token)
        current_session().invalidate()
        return current_session().payload()


@app.post("/api/confirm")
def confirm(payload: TokenPayload):
    with current_session().lock:
        session = current_session()
        check_token(session, payload.expected_generation_token)
        old_confirmed = session.data["request"]["confirmed"]
        data = deepcopy(session.data)
        data["request"]["confirmed"] = True
        for material in data["materials"]:
            if material["available"]:
                material["confirmed"] = True
        try:
            data = engine.validate_input(data)
        except engine.EngineError as exc:
            session.invalidate()
            raise HTTPException(status_code=422, detail=engine_detail(exc)) from exc
        changed = (data != session.data) or (not old_confirmed)
        session.invalidate(bump=changed)
        session.data = deepcopy(data)
        return session.payload()


def fallback_trace(requested, signature, code="AI_UNAVAILABLE", calls=0):
    return {"requested": requested, "used": False, "mode": "fallback" if requested else "manual", "code": code, "reason": "Pemilihan template deterministik; model live tidak digunakan.", "live": False, "live_model_used": False, "model": None, "call_count": calls, "input_signature": signature, "budgets": {"model_calls": 2, "call_timeout_s": AI_CALL_TIMEOUT_S, "action_deadline_s": ACTION_DEADLINE_S, "checks_per_variant": 50000, "seconds_per_variant": 5, "variants": 2}}


def checked_trace(raw, signature):
    if not isinstance(raw, dict) or type(raw.get("call_count", 0)) is not int or not 0 <= raw.get("call_count", 0) <= 2:
        raise engine.EngineError("INVALID_INPUT", "Trace tidak valid")
    trace = fallback_trace(True, signature, calls=raw.get("call_count", 0))
    live = raw.get("live_model_used") is True
    if live:
        trace.update(used=True, mode="live", code="AI_PROPOSAL_CHECKED", reason="Usulan terstruktur diperiksa; geometri ditentukan backend.", live=True, live_model_used=True, model=ai.model_name())
    safe_reasons = {"AI_NOT_CONFIGURED", "AI_ENDPOINT_REJECTED", "AI_REDIRECT_REJECTED", "AI_RESPONSE_TOO_LARGE", "AI_RESPONSE_INVALID", "AI_SCHEMA_INVALID", "AI_STORY_MISMATCH", "AI_HTTP_ERROR", "AI_TIMEOUT", "AI_CONNECTION_ERROR", "AI_REQUEST_CHECKED", "AI_UNSAFE_REQUEST", "AI_INPUT_REQUIRED", "AI_PLAN_CHECKED", "AI_GEOMETRY_REJECTED", "AI_CHANGE_CHECKED", "AI_CHANGE_NOT_ALLOWED"}
    if raw.get("reason") in safe_reasons:
        trace["reason"] = raw["reason"]
    if raw.get("mode") == "rejected" and raw.get("reason") == "AI_UNSAFE_REQUEST":
        trace.update(mode="rejected", code="UNSUPPORTED_REQUEST")
    if raw.get("mode") == "clarification" and raw.get("reason") == "AI_INPUT_REQUIRED":
        trace.update(mode="clarification", code="AI_INPUT_REQUIRED")
    for key in ("elapsed_s", "latency_s", "model_call_latency_s"):
        value = raw.get(key)
        if engine.finite(value) and 0 <= value <= ACTION_DEADLINE_S:
            trace[key] = value
    return trace


def manual_change(data):
    return {"action": "propose_supported_change", "variant": "front_split_center", "material_ids": [m["id"] for m in data["materials"] if m["available"] and m["confirmed"]]}


def validate_change(proposal, data):
    if not isinstance(proposal, dict) or set(proposal) != {"action", "variant", "material_ids"} or proposal["action"] != "propose_supported_change" or proposal["variant"] != "front_split_center" or data["request"]["max_front_splits"] != 1:
        raise engine.EngineError("INVALID_INPUT", "Usulan perubahan tidak didukung")
    ids = proposal["material_ids"]
    allowed = {m["id"] for m in data["materials"] if m["available"] and m["confirmed"]}
    if not isinstance(ids, list) or not ids or any(type(mid) is not str or mid not in allowed for mid in ids) or len(set(ids)) != len(ids):
        raise engine.EngineError("INVALID_INPUT", "ID bahan usulan tidak valid")
    validator = getattr(ai, "validate_change", None)
    if validator is not None:
        normalized = validator(deepcopy(proposal), deepcopy(data))
        if not isinstance(normalized, dict) or normalized != proposal:
            raise engine.EngineError("INVALID_INPUT", "Normalisasi usulan berbeda")
    return deepcopy(proposal)


def choose_change(data, failure, signature, use_ai, consent):
    proposal = manual_change(data)
    trace = fallback_trace(use_ai, signature, "AI_CONSENT_REQUIRED" if use_ai and not consent else "AI_UNAVAILABLE")
    if use_ai and consent and ai.available():
        try:
            proposed, raw_trace = ai.propose_supported_change(deepcopy(data), failure=deepcopy(failure))
            proposal = validate_change(proposed, data)
            trace = checked_trace(raw_trace, signature)
        except (engine.EngineError, ValueError, TypeError, KeyError):
            trace = fallback_trace(True, signature)
    return proposal, trace


def search(data, signature, variants):
    return engine.search_layout(deepcopy(data), signature, variants=variants, **SEARCH_BUDGET)


def global_area_failure(data):
    variants = ["whole"] + (["front_split_center"] if data["request"]["max_front_splits"] else [])
    available = sum(engine.raw_area(m) for m in data["materials"] if m["available"])
    minimum = min(engine.generate_patterns(data["request"], variant)["cut_area_mm2"] for variant in variants)
    return available < minimum - engine.EPS, available, minimum, variants


def merge_search(results):
    searches = [result.get("search", {}) for _, result in results]
    merged = {"checks_used": sum(s.get("checks_used", 0) for s in searches), "orders_used": sum(s.get("orders_used", 0) for s in searches), "elapsed_s": round(sum(s.get("elapsed_s", 0) for s in searches), 3), "variants": [v for s in searches for v in s.get("variants", [])], "grid_mm": 1, "limit_checks_per_variant": 50000, "limit_seconds_per_variant": 5, "phases": [{"variants": variants, "code": result.get("code", "FEASIBLE"), "search": deepcopy(result.get("search", {}))} for variants, result in results]}
    for key in ("available_area_mm2", "minimum_area_mm2", "allowed_variants"):
        for item in searches:
            if key in item:
                merged[key] = item[key]
                break
    return merged


@app.post("/api/plan")
def plan(payload: Optional[PlanPayload] = None):
    payload = payload or PlanPayload()
    with current_session().lock:
        session = current_session()
        check_token(session, payload.expected_generation_token)
        session.invalidate(bump=False)
        session.planning = True
        snapshot = capture(session)
        parent = session.previous_revision["revision"]["design_revision_id"] if session.previous_revision else None
    started = time.monotonic()
    trace = fallback_trace(payload.use_ai, snapshot.signature)
    results = []
    candidate = None
    failure = None
    metadata = {}
    try:
        first = search(snapshot.data, snapshot.signature, ["whole"])
        final = first
        lower, available_area, minimum_area, variants = global_area_failure(snapshot.data)
        if lower:
            final = {**first, "ok": False, "code": "AREA_LOWER_BOUND_FAIL", "message": "Luas mentah tersedia di bawah minimum varian yang diizinkan.", "search": {**first.get("search", {}), "available_area_mm2": available_area, "minimum_area_mm2": minimum_area, "allowed_variants": variants}}
        elif first["ok"] or first["code"] == "UNCONFIRMED_INPUT":
            final = first
        elif snapshot.data["request"]["max_front_splits"] == 1:
            proposal, trace = choose_change(snapshot.data, first, snapshot.signature, payload.use_ai, payload.use_ai and payload.ai_consent)
            with current_session().lock:
                check_snapshot(snapshot)
            split = search(snapshot.data, snapshot.signature, [proposal["variant"]])
            results.append(([proposal["variant"]], split))
            final = split
            if not split["ok"]:
                final = {**split, "code": "NO_LAYOUT_WITHIN_BUDGET", "message": "Tidak ditemukan tata letak dalam batas pencarian; bukan bukti mustahil."}
        results.append((["whole"], final))
        metadata = merge_search(results)
        trace["search"] = deepcopy(metadata)
        if time.monotonic() - started > ACTION_DEADLINE_S:
            final = {"ok": False, "code": "NO_LAYOUT_WITHIN_BUDGET", "message": "Batas waktu aksi tercapai."}
        if final["ok"]:
            candidate = deepcopy(final["candidate"])
            if payload.use_ai and payload.ai_consent:
                candidate, design_trace = ai.generate_plan(snapshot.data, candidate, snapshot.signature)
                with current_session().lock:
                    check_snapshot(snapshot)
                candidate["design_trace"] = {**design_trace, "input_signature": snapshot.signature}
                calls = trace.get("call_count", 0) + design_trace.get("call_count", 0)
                if design_trace.get("live_model_used") or not trace.get("live_model_used"):
                    trace = checked_trace(design_trace, snapshot.signature)
                trace["call_count"] = min(calls, 2)
                trace["design_source"] = design_trace["mode"]
                trace["search"] = deepcopy(metadata)
            activation = candidate["variant"] == "whole"
            report = engine.validate_candidate(snapshot.data, candidate, snapshot.signature, consent=None, activation=activation)
            if not (report["passed"] if activation else report["geometry_passed"]):
                final = {"ok": False, "code": "VALIDATION_FAILED", "message": "Kandidat gagal pemeriksaan backend.", "failures": report["failures"]}
                candidate = None
            else:
                candidate.update(validation_report={**report, "search": deepcopy(metadata)}, search=deepcopy(metadata), design_revision_id=str(uuid.uuid4()), parent_id=parent, job_token=snapshot.job_token, generation_token=snapshot.generation)
                candidate["explanation"] = ai.explain_checked_result(deepcopy(snapshot.data), deepcopy(candidate), deepcopy(report))
                candidate["human_summary"] = presentation.summary(snapshot.data, candidate)
                candidate["guide_steps"] = step_catalog.steps(snapshot.data, candidate)
                candidate["color_notice"] = presentation.color_notice(snapshot.data, candidate)
                if time.monotonic() - started > ACTION_DEADLINE_S:
                    final = {"ok": False, "code": "NO_LAYOUT_WITHIN_BUDGET", "message": "Batas waktu aksi tercapai; coba ulang."}
                    candidate = None
        failure = None if final["ok"] else {"code": final["code"], "message": final["message"], "search": metadata, "failures": final.get("failures", [])}
    except engine.EngineError as exc:
        failure = {**engine_detail(exc), "search": merge_search(results)}
    with current_session().lock:
        check_snapshot(snapshot)
        session.planning = False
        session.failure = deepcopy(failure)
        session.search = deepcopy(metadata)
        session.ai_trace = deepcopy(trace)
        if failure is not None:
            session.candidate = None
            session.active_revision = None
            session.stale = True
            raise HTTPException(status_code=422, detail=failure)
        session.candidate = deepcopy(candidate)
        session.active_revision = deepcopy(candidate) if candidate["variant"] == "whole" else None
        session.stale = False
        return session.payload()


@app.post("/api/accept-split")
def accept_split(payload: AcceptPayload):
    with current_session().lock:
        session = current_session()
        check_token(session, payload.expected_generation_token)
        candidate = session.candidate
        if not candidate or candidate.get("variant") != "front_split_center" or candidate.get("change_acceptance") is not None or session.active_revision is not None:
            raise error(400, "NO_PENDING_SPLIT", "Tidak ada usulan split yang menunggu persetujuan.")
        sig = engine.signature(session.data, None)
        if session.consent is not None or candidate.get("input_signature") != sig or candidate.get("job_token") != session.job_token or candidate.get("generation_token") != session.generation_token or (payload.candidate_signature is not None and payload.candidate_signature != sig):
            raise stale_error()
        if not payload.accepted:
            session.invalidate()
            return session.payload()
        consent = {"accepted": True, "additional_seams": 1, "variant": "front_split_center"}
        new_sig = engine.signature(session.data, consent)
        accepted = deepcopy(candidate)
        accepted.update(input_signature=new_sig, proposal_signature=sig, change_acceptance=deepcopy(consent))
        if accepted.get("ai_package"):
            accepted["ai_package"].update(input_signature=new_sig, proposal_signature=sig)
        report = engine.validate_candidate(session.data, accepted, new_sig, consent=consent, activation=True)
        if not report["passed"]:
            raise error(400, "VALIDATION_FAILED", "Revisi split gagal divalidasi ulang saat aktivasi.", failures=report["failures"])
        session.generation_token += 1
        session.job_token = str(uuid.uuid4())
        session.ai_job_token = str(uuid.uuid4())
        session.image_job_token = str(uuid.uuid4())
        session.image = None
        session.image_bytes = None
        session.clear_image_model()
        accepted.update(validation_report={**report, "search": deepcopy(candidate["search"])}, design_revision_id=str(uuid.uuid4()), parent_id=candidate["design_revision_id"], job_token=session.job_token, generation_token=session.generation_token)
        session.consent = deepcopy(consent)
        session.candidate = deepcopy(accepted)
        session.active_revision = deepcopy(accepted)
        session.stale = False
        session.failure = None
        return session.payload()


@app.get("/api/export-pdf")
def export_pdf(revision_sig: str, mode: Literal["paper_test", "fabric_trial"] = "paper_test", kind: Literal["guide", "technical"] = "guide"):
    with current_session().lock:
        session = current_session()
        check_token(session, None)
        session.refresh()
        sig = session.signature()
        if revision_sig != sig:
            raise stale_error()
        if not session.active_revision:
            if session.stale:
                raise stale_error()
            raise error(400, "NO_ACTIVE_REVISION", "Belum ada rencana tervalidasi yang aktif.")
        revision = session.active_revision
        if revision.get("input_signature") != sig or revision.get("job_token") != session.job_token or revision.get("generation_token") != session.generation_token:
            raise stale_error()
        snapshot = capture(session)
        revision_snapshot = deepcopy(revision)
        revision_id = revision["design_revision_id"]
        image = session.image
        illustration = session.image_bytes if image and image.get("input_signature")==sig and image.get("revision_id")==revision_id else None
    failure = None
    try:
        content = pdf_export.generate_pdf(deepcopy(snapshot.data), revision_snapshot, sig, mode=mode, kind=kind, illustration=illustration)
    except engine.EngineError as exc:
        failure = engine_detail(exc)
    with current_session().lock:
        check_snapshot(snapshot)
        if not session.active_revision or session.active_revision.get("design_revision_id") != revision_id or session.active_revision.get("input_signature") != sig:
            raise stale_error()
        if failure is not None:
            raise HTTPException(status_code=409 if failure["code"] == "STALE_REVISION" else 400, detail=failure)
    filename = f'SISA-petunjuk-pouch-{revision_id[:8]}.pdf' if kind == "guide" else f'SISA-lampiran-teknis-{revision_id[:8]}.pdf'
    return Response(content=content, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-SISA-Revision": revision_id, "X-Input-Signature": sig, "Cache-Control": "no-store"})


def render_preview_png(document_bytes, page):
    # Keep native PDF operations on one worker, without blocking session edits.
    import pymupdf
    with PDF_PREVIEW_LOCK:
        with pymupdf.open(stream=document_bytes, filetype="pdf") as document:
            return document[page-1].get_pixmap(matrix=pymupdf.Matrix(1.25,1.25), alpha=False).tobytes("png")


@app.get("/api/export-preview")
def export_preview(revision_sig: str, page: int = 1):
    if page not in (1, 2, 3):
        raise error(400, "INVALID_INPUT", "Pratinjau tersedia untuk tiga halaman pertama.")
    with current_session().lock:
        session = current_session()
        session.refresh()
        revision_id = (session.active_revision or {}).get("design_revision_id")
    document_bytes = export_pdf(revision_sig, mode="paper_test", kind="guide").body
    content = PDF_PREVIEW_WORKER.submit(render_preview_png, document_bytes, page).result()
    with current_session().lock:
        session.refresh()
        if not session.alive or session.signature()!=revision_sig or (session.active_revision or {}).get("design_revision_id")!=revision_id:
            raise stale_error()
    return Response(content=content, media_type="image/png", headers={"Cache-Control": "no-store", "X-SISA-Revision": revision_id})


def ai_snapshot(payload, require_confirmed=True):
    with current_session().lock:
        session = current_session()
        check_token(session, payload.expected_generation_token)
        if not payload.ai_consent:
            raise error(400, "AI_CONSENT_REQUIRED", "Persetujuan pengiriman data ke AI diperlukan.")
        if require_confirmed and not confirmed(session.data):
            raise error(422, "UNCONFIRMED_INPUT", "Konfirmasikan request dan semua bahan tersedia.")
        session.ai_job_token = str(uuid.uuid4())
        return capture(session)


@app.post("/api/ai/request")
def ai_request(payload: AiRequestPayload):
    snapshot = ai_snapshot(payload, require_confirmed=False)
    result = ai.unavailable_request()
    trace = fallback_trace(True, snapshot.signature)
    trace["reason"] = "AI_NOT_CONFIGURED"
    started = time.monotonic()
    if ai.available() or not payload.text.strip():
        try:
            context_data = deepcopy(snapshot.data)
            with current_session().lock:
                check_snapshot(snapshot, ai_job=True)
                context_data["_chat_context"] = {"messages": deepcopy(snapshot.session.chat_history), "earlier_brief": deepcopy(snapshot.session.chat_summary)}
            proposed, raw_trace = ai.propose_request(payload.text, context_data)
            if proposed["proposal"] is not None:
                proposed["proposal"] = engine.validate_input({"materials": deepcopy(snapshot.data["materials"]), "request": proposed["proposal"]})["request"]
                proposed["proposal"]["confirmed"] = False
            trace = checked_trace(raw_trace, snapshot.signature)
            if time.monotonic() - started <= ACTION_DEADLINE_S:
                result = deepcopy(proposed) if trace["live_model_used"] or trace["mode"] in ("rejected", "clarification") else ai.unavailable_request()
            else:
                trace = fallback_trace(True, snapshot.signature)
                trace["reason"] = "AI_TIMEOUT"
        except (engine.EngineError, ValueError, TypeError, KeyError):
            trace = fallback_trace(True, snapshot.signature)
            trace["reason"] = "AI_SCHEMA_INVALID"
    else:
        ai.log_request(payload.text, trace)
    with current_session().lock:
        check_snapshot(snapshot, ai_job=True)
        snapshot.session.ai_trace = deepcopy(trace)
    return {**deepcopy(result), "trace": deepcopy(trace)}


def chat_event(name, value):
    return "event: " + name + "\ndata: " + json.dumps(value, ensure_ascii=False) + "\n\n"


@app.post("/api/ai/chat")
def ai_chat(payload: ChatPayload):
    with current_session().lock:
        session = current_session()
        check_token(session, payload.expected_generation_token)
        if not payload.ai_consent:
            raise error(400, "AI_CONSENT_REQUIRED", "Persetujuan AI diperlukan.")
        if session.chat_calls >= 20:
            raise error(429, "CHAT_LIMIT", "Batas obrolan sesi tercapai.")
        session.chat_calls += 1
        initial = capture(session)
        previous = deepcopy(session.active_revision)
        session.chat_history.append({"role": "user", "text": ai.redact_text(payload.text)})
        if len(session.chat_history) > 20:
            session.chat_summary = deepcopy(session.data["request"])
            session.chat_history = session.chat_history[-20:]

    action = chat_actions.intent(payload.text)

    def events():
        try:
            if action is not None:
                with current_session().lock:
                    check_snapshot(initial)
                    candidate = deepcopy(session.active_revision or session.candidate)
                    checked = candidate and candidate.get("input_signature") == session.signature() and candidate.get("validation_report", {}).get("geometry_passed")
                if not checked:
                    reply = "Buat rancangan dulu, supaya penjelasan dan gambar mengikuti kainmu."
                elif action["action"] == "regenerate_image":
                    yield chat_event("step", {"text": "Menyiapkan ilustrasi sesuai rancanganmu"})
                    info = ai_image(ImagePayload(ai_consent=True, regenerate=action["regenerate"], expected_generation_token=initial.generation))
                    yield chat_event("image", info)
                    reply = "Ilustrasi sudah mengikuti rancanganmu. Bentuk dan pola tetap sama."
                else:
                    reply = chat_actions.explanation(initial.data, candidate, action)
                with current_session().lock:
                    check_snapshot(initial)
                    session.chat_history.append({"role": "assistant", "text": reply})
                    session.chat_history = session.chat_history[-20:]
                yield chat_event("message", {"text": reply, "status": "ready"})
                yield chat_event("done", {"ok": True})
                return
            yield chat_event("step", {"text": "Membaca ceritamu"})
            with current_session().lock:
                check_snapshot(initial)
            result = ai_request(AiRequestPayload(text=payload.text, ai_consent=True, expected_generation_token=initial.generation))
            yield chat_event("step", {"text": "Mencocokkan dengan kainmu"})
            with current_session().lock:
                check_snapshot(initial)
                session.chat_history.append({"role": "assistant", "text": result["reply"]})
                session.chat_history = session.chat_history[-20:]
                if len(session.chat_history) == 20:
                    session.chat_summary = deepcopy(session.data["request"])
            yield chat_event("message", {"text": result["reply"], "status": result["status"]})
            yield chat_event("brief", result)
            for item in result["questions"]:
                yield chat_event("question", item)
            for item in result["warnings"]:
                yield chat_event("warning", item)
            # Only this typed, independently checked request action may change data.
            # Initial briefs await user acceptance; explicit revisions reuse confirmed materials.
            if payload.replan and previous and result["proposal"] and not result["warnings"]:
                with current_session().lock:
                    check_snapshot(initial)
                    draft = deepcopy(session.data)
                    draft["request"] = deepcopy(result["proposal"])
                    update_input(InputPayload(data=draft, expected_generation_token=session.generation_token))
                    confirm(TokenPayload(expected_generation_token=session.generation_token))
                    generation = session.generation_token
                yield chat_event("step", {"text": "Mencari susunan potongan"})
                planned = plan(PlanPayload(use_ai=False, ai_consent=True, expected_generation_token=generation))
                with current_session().lock:
                    if not session.alive or session.generation_token != planned["generation_token"] or session.signature() != planned["signature"]:
                        raise stale_error()
                    session.ai_trace = {**result["trace"], "design_source": "automatic_after_chat"}
                    planned = session.payload()
                yield chat_event("step", {"text": "Mengecek 10 hal"})
                yield chat_event("design_ready", planned)
            yield chat_event("done", {"ok": True})
        except HTTPException as exc:
            yield chat_event("error", exc.detail)
            yield chat_event("done", {"ok": False})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@app.post("/api/ai/image")
def ai_image(payload: ImagePayload):
    snapshot = ai_snapshot(payload)
    with current_session().lock:
        check_snapshot(snapshot)
        candidate = deepcopy(snapshot.session.active_revision or snapshot.session.candidate)
        if not candidate or not candidate.get("validation_report", {}).get("geometry_passed"):
            raise error(400, "NO_CHECKED_PLAN", "Buat rencana yang lolos pemeriksaan sebelum membuat gambar pouch.")
        cache_key = (snapshot.signature, hashlib.sha256(ai.image_prompt(snapshot.data, candidate).encode()).hexdigest())
        if not payload.regenerate and cache_key in snapshot.session.image_cache:
            content, cached = snapshot.session.image_cache[cache_key]
            snapshot.session.image_bytes = content
            snapshot.session.image_job_token = str(uuid.uuid4())
            snapshot.session.clear_image_model()
            snapshot.session.image = {**cached, "source": "cached", "call_count": 0, "input_signature": snapshot.signature, "revision_id": candidate["design_revision_id"], "image_id": snapshot.session.image_job_token}
            return deepcopy(snapshot.session.image)
        revision_id = candidate["design_revision_id"]
        attempts = snapshot.session.image_attempts.get(revision_id, 0)
        if attempts >= 4 or snapshot.session.image_calls >= 20:
            raise error(429, "AI_IMAGE_LIMIT", "Jatah pembuatan gambar sudah dipakai. Rancangan dan pola tetap aman.")
        snapshot.session.image_calls += 1
        snapshot.session.image_attempts[revision_id] = attempts+1
        image_token = str(uuid.uuid4())
        snapshot.session.image_job_token = image_token
        snapshot.session.image = None
        snapshot.session.image_bytes = None
        snapshot.session.clear_image_model()
    try:
        content, info = ai.generate_image(snapshot.data, candidate)
    except (OSError, ValueError, TypeError, KeyError):
        with current_session().lock:
            check_snapshot(snapshot)
            if snapshot.session.image_job_token != image_token:
                raise stale_error()
        raise error(502, "AI_IMAGE_UNAVAILABLE", "Gambar AI belum berhasil dibuat. Rencana dan pola tetap tersedia; coba buat gambar lagi.")
    with current_session().lock:
        check_snapshot(snapshot)
        if snapshot.session.image_job_token != image_token:
            raise stale_error()
        snapshot.session.image_bytes = content
        snapshot.session.image = {**info, "input_signature": snapshot.signature, "revision_id": candidate["design_revision_id"], "image_id": image_token}
        snapshot.session.image_cache[cache_key] = (content, deepcopy(info))
        while len(snapshot.session.image_cache)>8:
            snapshot.session.image_cache.pop(next(iter(snapshot.session.image_cache)))
        return deepcopy(snapshot.session.image)


@app.post("/api/ai/image/cancel")
def cancel_image(payload: TokenPayload):
    with current_session().lock:
        check_token(current_session(), payload.expected_generation_token)
        current_session().image_job_token = str(uuid.uuid4())
        current_session().image = None
        current_session().image_bytes = None
        current_session().clear_image_model()
        return {"cancelled": True}


@app.get("/api/ai/image")
def read_ai_image(revision_sig: str, revision_id: str, image_id: str):
    with current_session().lock:
        check_token(current_session(), None)
        current_session().refresh()
        image = current_session().image
        candidate = current_session().active_revision or current_session().candidate
        if not image or not current_session().image_bytes or not candidate or revision_sig != current_session().signature() or image.get("input_signature") != revision_sig or image.get("revision_id") != revision_id or candidate.get("design_revision_id") != revision_id or image.get("image_id") != image_id:
            raise stale_error()
        return Response(content=current_session().image_bytes, media_type=image["media_type"], headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


def checked_image(session, image_id):
    session.refresh()
    candidate = session.active_revision or session.candidate
    image = session.image
    if not candidate or not image or not session.image_bytes or image.get('image_id') != image_id or image.get('input_signature') != session.signature() or image.get('revision_id') != candidate.get('design_revision_id'):
        raise stale_error()
    return image


@app.post('/api/ai/image-model')
def create_image_model(payload: ImageModelPayload):
    session = current_session()
    with session.lock:
        check_token(session, payload.expected_generation_token)
        image = deepcopy(checked_image(session, payload.image_id))
        if session.image_model and session.image_model.get('image_id') == payload.image_id and session.image_model_bytes:
            return deepcopy(session.image_model)
        if not image_model.available():
            raise error(503, 'IMAGE_MODEL_UNAVAILABLE', 'Rekonstruksi 3D lokal belum tersedia di perangkat ini.')
        snapshot = capture(session)
        content = session.image_bytes
        revision = session.active_revision or session.candidate
        back = next(p for p in revision['placements'] if p['instance_id'] == 'BACK')
        material = next(m for m in session.data['materials'] if m['id'] == back['material_id'])
        back_hex = colors.resolve(material['color'])['hex']
        token = str(uuid.uuid4())
        session.image_model_job_token = token
    try:
        model, info = image_model.reconstruct(content, back_hex)
    except (OSError, ValueError, TypeError, ImportError):
        with session.lock:
            check_snapshot(snapshot)
            checked_image(session, payload.image_id)
        raise error(422, 'IMAGE_MODEL_FAILED', 'Bentuk pouch belum bisa dibaca dari ilustrasi ini. Coba buat ulang gambar; model pola tetap tersedia.')
    with session.lock:
        check_snapshot(snapshot)
        checked_image(session, payload.image_id)
        if session.image_model_job_token != token:
            raise stale_error()
        session.image_model_bytes = model
        session.image_model = {**info, 'model_id': token, 'image_id': image['image_id'], 'input_signature': image['input_signature'], 'revision_id': image['revision_id']}
        return deepcopy(session.image_model)


@app.get('/api/ai/image-model')
def read_image_model(image_id: str, model_id: str, revision_sig: str, revision_id: str):
    session = current_session()
    with session.lock:
        check_token(session, None)
        image = checked_image(session, image_id)
        model = session.image_model
        if not model or not session.image_model_bytes or model.get('model_id') != model_id or model.get('image_id') != image_id or image['input_signature'] != revision_sig or image['revision_id'] != revision_id:
            raise stale_error()
        return Response(content=session.image_model_bytes, media_type='model/gltf-binary', headers={'X-Content-Type-Options': 'nosniff'})


@app.post("/api/ai/propose")
def ai_propose(payload: Optional[AiConsentPayload] = None):
    payload = payload or AiConsentPayload()
    snapshot = ai_snapshot(payload)
    started = time.monotonic()
    first = search(snapshot.data, snapshot.signature, ["whole"])
    with current_session().lock:
        check_snapshot(snapshot, ai_job=True)
    proposal = None
    trace = fallback_trace(True, snapshot.signature)
    if not first["ok"] and first["code"] == "NO_LAYOUT_WITHIN_BUDGET" and snapshot.data["request"]["max_front_splits"] == 1:
        proposal, trace = choose_change(snapshot.data, first, snapshot.signature, True, True)
    if time.monotonic() - started > ACTION_DEADLINE_S:
        proposal = None
        trace = fallback_trace(True, snapshot.signature)
    trace["search"] = deepcopy(first.get("search", {}))
    with current_session().lock:
        check_snapshot(snapshot, ai_job=True)
        snapshot.session.ai_trace = deepcopy(trace)
    return {"proposal": deepcopy(proposal), "trace": deepcopy(trace)}


@app.get("/api/projects")
def list_projects():
    session = current_session()
    with session.lock:
        check_token(session, None)
        return {"projects": current_registry().store.list_projects(current_owner()),
                "autosave": {"saved_at": session.autosaved_at}, "retention_days": RETENTION_DAYS}


@app.post("/api/projects/save")
def save_project(payload: ProjectSavePayload):
    session = current_session()
    with session.lock:
        check_token(session, payload.expected_generation_token)
        name = payload.name.strip()
        if not name or any(not character.isprintable() for character in name):
            raise error(422, "INVALID_INPUT", "Beri nama singkat untuk pekerjaanmu, tanpa baris atau karakter khusus.")
        current_registry().save(current_owner(), session)
        try:
            project = current_registry().store.save_project(current_owner(), name, document(session))
        except ValueError:
            raise error(429, "PROJECT_LIMIT", "Kamu bisa menyimpan dua puluh pekerjaan. Hapus simpanan lama untuk menambah yang baru.")
        current_registry().save(current_owner(), session)
        return {"project": project, "state": session.payload()}


@app.post("/api/projects/open")
def open_project(payload: ProjectOpenPayload):
    session = current_session()
    with session.lock:
        check_token(session, payload.expected_generation_token)
        saved = current_registry().store.open_project(current_owner(), payload.project_id)
        if saved is None:
            raise error(404, "PROJECT_NOT_FOUND", "Simpanan ini tidak ditemukan di browsermu.")
        try:
            if saved.get('record_version') != 1:
                raise ValueError('UNSUPPORTED_RECORD')
            engine.validate_input(saved['data'])
        except (ValueError, TypeError, KeyError, engine.EngineError):
            raise error(409, "PROJECT_INVALID", "Simpanan ini belum bisa dibuka. Pekerjaanmu sekarang tetap aman.")
        replacement = restore(saved)
        replacement.generation_token = max(session.generation_token, replacement.generation_token) + 1
        for revision in (replacement.candidate, replacement.active_revision):
            if revision:
                revision['generation_token'] = replacement.generation_token
        replacement.chat_calls = max(session.chat_calls, replacement.chat_calls)
        replacement.image_calls = max(session.image_calls, replacement.image_calls)
        replacement.telemetry_enabled = session.telemetry_enabled
        replacement.anonymous_id = session.anonymous_id
        for key, count in session.image_attempts.items():
            replacement.image_attempts[key] = max(count, replacement.image_attempts.get(key, 0))
        current_registry().replace(current_owner(), session, replacement)
        return replacement.payload()


@app.post("/api/projects/delete")
def delete_project(payload: ProjectOpenPayload):
    session = current_session()
    with session.lock:
        check_token(session, payload.expected_generation_token)
        deleted = current_registry().store.delete_project(current_owner(), payload.project_id)
        if not deleted:
            raise error(404, "PROJECT_NOT_FOUND", "Simpanan ini tidak ditemukan di browsermu.")
    return {"deleted": True}


@app.post("/api/telemetry/consent")
def telemetry_consent(payload: TelemetryConsentPayload):
    session = current_session()
    with session.lock:
        check_token(session, None)
        session.telemetry_enabled = payload.enabled
        if not payload.enabled:
            current_registry().store.forget_events(session.anonymous_id)
            session.anonymous_id = str(uuid.uuid4())
        current_registry().save(current_owner(), session)
        return {"enabled": session.telemetry_enabled}


@app.post("/api/telemetry/event")
def telemetry_event(payload: TelemetryEventPayload):
    session = current_session()
    with session.lock:
        check_token(session, None)
        recorded = session.telemetry_enabled and current_registry().store.record_event(session.anonymous_id, payload.event)
        return {"recorded": recorded}
