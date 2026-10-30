"""특허기술 전용 챗봇의 대화 한 차례 흐름(실제 LangGraph StateGraph).

장부 불러오기 → 목적 판정 → 근거 선별 → 응답 생성 → 목표 검사 → 독립 검증
→ (실패 시 실패 기록·엔진 교체 후 응답 생성으로 되돌아감) → 세대 대조
→ 문장 대조와 격리 → 영수증 기록 → 출력.

종료 조건
- OUTPUT : 출력 단계의 마지막 세대 대조까지 통과.
- HOLD   : 업무가 활성 아님, 분야·목적 판정 실패, 자격 있는 근거 없음, 검증 모델이
           같은 계열, 모든 엔진 격리, 통과 문장 없음, 영수증 불완전.
- STALE  : 처리 시작 세대와 현재 세대가 달라 늦은 응답을 폐기(장부에 영구 기록).
엔진 실패 횟수는 장부에 저장되고 엔진마다 한도가 있으므로 되돌아가는 고리는
반드시 끝난다. 그래프 재귀 한도도 이 최대 횟수로 계산해 둔다.

langgraph는 선택 의존성이다(requirements-dreamkeeper-graph.txt). 이 모듈을
불러올 때만 필요하며 주 프로그램은 요청을 처리할 때까지 불러오지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.dreamkeeper_chat_core import (
    ChatHold, DreamkeeperChatCore, EngineSpec, VerifierSpec, same_family,
)
from app.services.dreamkeeper_engine import EngineHold


@dataclass(frozen=True)
class ChatRuntime:
    """주입된 외부 모델 묶음. 엔진 순서가 교체 순서다."""
    engines: tuple[EngineSpec, ...]
    verifier: VerifierSpec
    purpose_classifier: Callable[[str], str] | None = None

    def __post_init__(self):
        if not self.engines or not all(isinstance(e, EngineSpec) for e in self.engines):
            raise ValueError("at least one EngineSpec required")
        if len({e.name for e in self.engines}) != len(self.engines):
            raise ValueError("engine names must be unique")
        if not isinstance(self.verifier, VerifierSpec):
            raise ValueError("VerifierSpec required")
        for engine in self.engines:
            if same_family(engine.family, self.verifier.family):
                raise ValueError(f"verifier family equals worker family of {engine.name}")


class ChatState(TypedDict, total=False):
    logical_id: str
    task_id: str
    question: str
    purpose: str | None
    domain: str
    turn_id: str | None
    engine: str | None
    route: str
    status: str
    hold_reason: str | None
    output: dict | None
    engine_failures: list


def build_chat_graph(core: DreamkeeperChatCore, runtime: ChatRuntime):
    specs = {spec.name: spec for spec in runtime.engines}

    def key(state):
        return state["logical_id"], state["task_id"], state["turn_id"]

    def turn_end(state, status=None):
        logical, task, tid = key(state)
        turn = core.turn(logical, task, tid)["state"]
        return {"route": "end", "status": status or turn["status"],
                "hold_reason": turn.get("hold_reason")}

    def load_ledger(state: ChatState) -> dict:
        try:
            tid = core.start_turn(state["logical_id"], state["task_id"],
                                  question=state["question"], purpose=state.get("purpose"),
                                  domain=state["domain"])
        except (ChatHold, EngineHold, ValueError) as exc:
            return {"route": "end", "status": "HOLD", "turn_id": None,
                    "hold_reason": f"ledger unavailable: {exc}"}
        return {"turn_id": tid, "route": "next", "status": "RUNNING", "engine_failures": []}

    def judge_purpose(state: ChatState) -> dict:
        purpose = core.judge_purpose(*key(state), classifier=runtime.purpose_classifier)
        return turn_end(state) if purpose is None else {"purpose": purpose, "route": "next"}

    def select_evidence(state: ChatState) -> dict:
        selection = core.select_evidence(*key(state))
        return turn_end(state) if selection is None else {"route": "next"}

    def failure(state: ChatState, reason: str) -> dict:
        logical, task, tid = key(state)
        entry = core.record_failure(logical, task, tid, engine_name=state["engine"], reason=reason)
        failures = list(state.get("engine_failures", [])) + [
            {"engine": state["engine"], "reason": reason, "quarantined": entry["quarantined"]}]
        return {"route": "failed", "engine_failures": failures}

    def generate(state: ChatState) -> dict:
        logical, task, tid = key(state)
        spec = core.next_engine(logical, task, list(runtime.engines))
        if spec is None:
            core.hold_turn(logical, task, tid, reason="all engines quarantined")
            return turn_end(state, "HOLD")
        try:
            binding = core.begin_attempt(logical, task, tid, spec)
        except (ChatHold, EngineHold) as exc:
            core.hold_turn(logical, task, tid, reason=f"engine run refused: {exc}")
            return turn_end(state, "HOLD")
        try:
            core.generate(logical, task, tid, binding, spec)
        except Exception as exc:  # 생성 함수 오류·형식 오류·엔진 거부
            if not core.check_generation(logical, task, tid, stage="generate"):
                return {**turn_end(state, "STALE"), "engine": spec.name}
            return {**failure({**state, "engine": spec.name},
                              f"generation failed: {type(exc).__name__}"), "engine": spec.name}
        return {"engine": spec.name, "route": "next"}

    def goal_check(state: ChatState) -> dict:
        result = core.check_goals(*key(state))
        if result["ok"]:
            return {"route": "next"}
        return failure(state, "goal drift: " + "; ".join(map(str, result.get("reasons", []))))

    def verify(state: ChatState) -> dict:
        try:
            result = core.verify(*key(state), runtime.verifier, specs[state["engine"]])
        except ChatHold as exc:
            core.hold_turn(*key(state), reason=str(exc))
            return turn_end(state, "HOLD")
        if result["ok"]:
            return {"route": "next"}
        return failure(state, "independent verification failed")

    def generation_check(state: ChatState) -> dict:
        ok = core.check_generation(*key(state), stage="before_sentences")
        return {"route": "next"} if ok else turn_end(state, "STALE")

    def sentence_filter(state: ChatState) -> dict:
        passed = core.filter_sentences(*key(state))
        if passed:
            return {"route": "next"}
        core.hold_turn(*key(state), reason="no sentence met support threshold")
        return turn_end(state, "HOLD")

    def record_receipt(state: ChatState) -> dict:
        oid = core.record_receipt(*key(state))
        return {"route": "next"} if oid else turn_end(state)

    def output(state: ChatState) -> dict:
        result = core.emit_output(*key(state))
        if result is None:
            return turn_end(state)
        return {"route": "end", "status": "OUTPUT", "output": result, "hold_reason": None}

    graph = StateGraph(ChatState)
    order = [("load_ledger", load_ledger), ("judge_purpose", judge_purpose),
             ("select_evidence", select_evidence), ("generate", generate),
             ("goal_check", goal_check), ("verify", verify),
             ("generation_check", generation_check), ("sentence_filter", sentence_filter),
             ("record_receipt", record_receipt), ("output", output)]
    for name, fn in order:
        graph.add_node(name, fn)
    graph.add_edge(START, "load_ledger")
    for (name, _), (following, _) in zip(order, order[1:]):
        targets = {"next": following, "end": END}
        if name in {"generate", "goal_check", "verify"}:
            targets["failed"] = "generate"  # 실패 기록 뒤 같은/다음 엔진으로 되돌아감
        graph.add_conditional_edges(name, lambda s: s["route"], targets)
    graph.add_edge("output", END)
    return graph.compile()


def run_chat_turn(core: DreamkeeperChatCore, runtime: ChatRuntime, *, logical_id: str,
                  task_id: str, question: str, domain: str, purpose: str | None = None) -> dict:
    graph = build_chat_graph(core, runtime)
    # 최대 시도 = 엔진 수 × 실패 한도. 시도마다 노드 3개 이내, 나머지 노드 10개.
    limit = 20 + 4 * len(runtime.engines) * core.config.max_failures_per_engine
    final = graph.invoke({"logical_id": logical_id, "task_id": task_id, "question": question,
                          "purpose": purpose, "domain": domain},
                         config={"recursion_limit": limit})
    return {"status": final.get("status"), "turn_id": final.get("turn_id"),
            "engine": final.get("engine"), "output": final.get("output"),
            "hold_reason": final.get("hold_reason"),
            "engine_failures": final.get("engine_failures", [])}
