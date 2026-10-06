"""Provider-isolated generation of alternative answers and explanations.

The provider's self-ranking or generation order is explicitly labelled; no
token likelihood or calibrated correctness probability is fabricated.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from types import SimpleNamespace
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.ranked_candidates import Candidate, SearchScope, VerificationCriteria
from app.services.cause_selection import CauseMapRequest


class CandidateGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["openai", "gemini", "claude"]
    explicit_authorization: Literal[True]
    context_ref: str = Field(min_length=1, max_length=500)
    source_text: str = Field(min_length=1, max_length=20000)
    task_text: str = Field(min_length=1, max_length=5000)
    student_response: str = Field(default="", max_length=5000)
    response_id: str | None = Field(default=None, max_length=100)
    count: int = Field(default=14, ge=1, le=50)
    subject: str = Field(default="영어", max_length=100)
    grade: str = Field(default="1학년", max_length=100)
    search: SearchScope = Field(default_factory=lambda: SearchScope(max_rank=14))
    verification: VerificationCriteria = Field(default_factory=VerificationCriteria)
    use_verified_reason_context: bool = False
    cause_map_request: CauseMapRequest | None = None


class CandidateGenerationError(RuntimeError):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code = status, code
        super().__init__(message)


def runtime_environment():
    """Reuse the deployed provider controls and clients; import only on a call."""
    try:
        from app.services import auto_generator as generator
        from app.services.control_plane import assert_provider_allowed, ProviderHeld
        from app.services.privacy_guard import assert_provider_zdr, ProviderZDRHeld
        return SimpleNamespace(generator=generator, assert_provider_allowed=assert_provider_allowed,
                               assert_provider_zdr=assert_provider_zdr,
                               held_errors=(ProviderHeld, ProviderZDRHeld))
    except ImportError as exc:
        raise CandidateGenerationError(503, "NO_RUNTIME_PROVIDER_MODULES",
                                       "서버 공급자 연결 구성요소가 준비되지 않았습니다. 후보 가져오기와 교사 직관 제안을 사용할 수 있습니다.") from exc


INSTRUCTIONS = """You are an EduRecover alternative-hypothesis authoring agent.
Use only the supplied task, source and authorized teaching context. Generate
diverse alternative answers/explanations for teacher review, including unusual
but source-supported candidates. A student's wrong answer does not prove one
particular misconception. Do not assert a diagnosis or correctness verification.
Do not disclose unrelated private assets, credentials or chat history.
Return exactly the requested number of distinct candidates as JSON only:
{"candidates":[{"text":"candidate answer and reasoning",
"self_reported_rank":1,"hypothesis_type":"declared answer/explanation type",
"concept_refs":["required-condition concept IDs"],
"evidence_refs":["specific supplied source location"]}]}
Rank is your self-reported plausibility ordering, not measured probability.
Do not output invented log probabilities or calibrated confidence scores.
"""


def parse_candidates(text: str, count: int, invocation_id: str) -> list[Candidate]:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        payload = json.loads(clean)
        items = payload["candidates"]
        if not isinstance(items, list) or len(items) != count:
            raise ValueError("Candidate count mismatch")
        candidates = []
        self_ranks = [item.get("self_reported_rank") for item in items]
        ranked = all(type(rank) is int and 1 <= rank <= 100 for rank in self_ranks) and len(set(self_ranks)) == count
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict) or not isinstance(item.get("text"), str):
                raise ValueError("Invalid candidate")
            refs = item.get("evidence_refs", [])
            if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
                raise ValueError("Invalid source references")
            candidates.append(Candidate(candidate_id="generated-" + invocation_id[:12] + "-" + str(index),
                origin="MODEL", rank=self_ranks[index - 1] if ranked else index,
                rank_basis="SELF_REPORTED_RANK" if ranked else "GENERATION_ORDER",
                model_score=None, score_basis="NOT_AVAILABLE_NO_MEASURED_MODEL_SCORE",
                source_ref="provider-invocation:" + invocation_id,
                text=item["text"], evidence_refs=refs,
                concept_refs=item.get("concept_refs", []),
                hypothesis_type=item.get("hypothesis_type", "ALTERNATIVE_ANSWER")))
        if len({candidate.text.strip() for candidate in candidates}) != count:
            raise ValueError("Duplicate candidates")
        return candidates
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        raise CandidateGenerationError(502, "INVALID_CANDIDATE_OUTPUT",
                                       "AI 후보 응답 형식이나 후보 수를 확인하지 못했습니다. 검증·수업 공개는 진행되지 않았습니다.") from exc


async def generate_candidates(req: CandidateGenerationRequest, *, verified_reason_memory: list[dict] | None = None,
                              approved_cause_context: dict | None = None) -> tuple[list[Candidate], dict]:
    environment = runtime_environment()
    generator = environment.generator
    try:
        decision = environment.assert_provider_allowed(req.provider, "generate")
        environment.assert_provider_zdr(req.provider)
    except environment.held_errors as exc:
        raise CandidateGenerationError(423, "PROVIDER_EXECUTION_HELD", str(exc)) from exc
    settings = generator.settings
    context = generator.build_authoring_context(provider=req.provider, subject=req.subject,
                grade=req.grade, source_text=req.source_text, question_types=["ALTERNATIVE_HYPOTHESIS"])
    prompt = json.dumps({"task": req.task_text, "student_response": req.student_response,
                        "source_text": req.source_text, "requested_count": req.count,
                        "authorized_teaching_context": context,
                        "teacher_approved_fixed_graph_context": approved_cause_context,
                        "teacher_approved_verified_reason_context":
                            (verified_reason_memory or []) if req.use_verified_reason_context else []}, ensure_ascii=False)
    instructions = generator.AI_ACCESS_NOTICE + INSTRUCTIONS
    invocation_id = uuid.uuid4().hex
    client = None
    try:
        if req.provider == "openai":
            if settings.openai_api_key is None:
                raise generator.GeneratorNotConfigured("OpenAI 서버 API 인증 설정이 없습니다.")
            model = settings.openai_model
            client = generator.AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
            response = await client.responses.create(model=model, instructions=instructions, input=prompt, store=False)
            text = response.output_text
        elif req.provider == "claude":
            if settings.anthropic_api_key is None:
                raise generator.GeneratorNotConfigured("Claude 서버 API 인증 설정이 없습니다.")
            model = settings.anthropic_model
            client = generator.AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
            response = await client.messages.create(model=model, max_tokens=12000, system=instructions,
                                                     messages=[{"role": "user", "content": prompt}])
            text = "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
        else:
            model = settings.gemini_model
            client = generator._vertex_gemini_client()
            response = await client.aio.models.generate_content(model=model, contents=prompt,
                config=generator.gemini_generation_config(instructions))
            text = response.text
        candidates = parse_candidates(text, req.count, invocation_id)
    except CandidateGenerationError:
        raise
    except generator.GeneratorNotConfigured as exc:
        raise CandidateGenerationError(503, "PROVIDER_NOT_CONFIGURED", str(exc)) from exc
    except Exception as exc:
        failure = generator.provider_failure(req.provider, exc)
        raise CandidateGenerationError(failure.http_status, failure.code, str(failure)) from exc
    finally:
        if client is not None:
            if req.provider == "gemini":
                await client.aio.aclose()
                client.close()
            else:
                await client.close()
    receipt = {"invocation_id": invocation_id, "provider": req.provider, "model": model,
               "fallback_used": False, "requested_count": req.count,
               "request_digest": hashlib.sha256(prompt.encode()).hexdigest(),
               "candidate_digest": hashlib.sha256(json.dumps([candidate.model_dump() for candidate in candidates],
                                                            sort_keys=True).encode()).hexdigest(),
               "control_epoch": getattr(decision, "control_epoch", None),
               "measured_probabilities_available": False, "correctness_verified": False}
    receipt["verified_reason_context_used"] = bool(req.use_verified_reason_context and verified_reason_memory)
    receipt["verified_reason_dependencies"] = [{
        "batch_id": memory["batch_id"], "candidate_id": memory["candidate_id"],
        "receipt_id": memory["receipt_id"], "receipt_digest": memory["receipt_digest"],
        "source_binding_digest": memory["source_binding_digest"],
        "reason_digest": hashlib.sha256(memory["verified_reason"].encode()).hexdigest(),
        "evidence_refs": memory["evidence_refs"]}
        for memory in (verified_reason_memory or []) if req.use_verified_reason_context]
    return candidates, receipt
