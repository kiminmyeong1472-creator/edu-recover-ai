"""API router for Redis-backed oral-interview practice."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.classroom_tutor import db, ensure_material_release, get_session_or_404, row_to_session
from app.services.auto_generator import GenerationFailed, GeneratorNotConfigured
from app.services.control_plane import ProviderHeld
from app.services.interview_memory import append_turn, context_lock, get_context
from app.services.oral_interview import evaluate_oral_interview
from app.services.privacy_guard import ProviderZDRHeld


oral_interview_router = APIRouter(prefix="/oral-interview", tags=["oral-interview"])


class OralInterviewEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=4, max_length=80)
    join_token: str = Field(min_length=6, max_length=128)
    student_alias: str = Field(min_length=1, max_length=60)
    question_id: str = Field(min_length=1, max_length=100)
    current_step: int = Field(ge=1, le=3)
    answer_reference: str = Field(default="", max_length=8000)
    student_answer: str = Field(min_length=1, max_length=12000)


def _authorize_student(req: OralInterviewEvaluationRequest) -> None:
    with db() as conn:
        row = get_session_or_404(conn, req.session_id)
        if req.join_token != row["join_token"]:
            raise HTTPException(403, "invalid join token")
        ensure_material_release(row_to_session(row))


@oral_interview_router.post("/api/evaluate")
async def evaluate(req: OralInterviewEvaluationRequest) -> dict:
    _authorize_student(req)
    try:
        async with context_lock(req.session_id, req.student_alias, req.question_id):
            context = await get_context(req.session_id, req.student_alias, req.question_id)
            result = await evaluate_oral_interview(
                current_step=req.current_step,
                answer_reference=req.answer_reference,
                context=context,
                student_answer=req.student_answer,
            )
            user_message = {
                "role": "user",
                "content": f"[Step {req.current_step} 답안 제출] {req.student_answer}",
            }
            assistant_message = {
                "role": "assistant",
                "content": (
                    f"[진단 레벨: {result['diagnostic_level']}] "
                    f"{result['feedback_message']}\n꼬리질문: {result['fallback_hint']}"
                ),
            }
            await append_turn(
                req.session_id,
                req.student_alias,
                req.question_id,
                user_message,
                assistant_message,
            )
            return result
    except TimeoutError as exc:
        raise HTTPException(409, "같은 문항의 이전 답변을 처리 중입니다. 완료 후 다시 제출해 주세요.") from exc
    except RuntimeError as exc:
        raise HTTPException(503, "구술면접 단기기억 저장소가 준비되지 않았습니다.") from exc
    except (ProviderHeld, ProviderZDRHeld) as exc:
        raise HTTPException(423, str(exc)) from exc
    except GeneratorNotConfigured as exc:
        raise HTTPException(503, str(exc)) from exc
    except GenerationFailed as exc:
        raise HTTPException(exc.http_status, str(exc), headers={"X-EduRecover-Error-Code": exc.code}) from exc
