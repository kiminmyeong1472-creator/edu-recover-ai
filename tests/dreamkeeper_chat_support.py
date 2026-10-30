"""챗봇 핵심부 시험용 공용 준비물. 가짜(대체) 모델 응답만 쓰며 외부 호출이 없다."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.services.dreamkeeper_chat_core import (
    DEFAULT_CONFIG_PATH, DreamkeeperChatCore, EngineSpec, VerifierSpec, parse_config,
)
from app.services.dreamkeeper_engine import DreamkeeperEngine

L, T = "subject-1", "task-1"
GOALS = {"g1": "광합성 과정을 설명한다", "g2": "근거 원문을 지킨다"}
S1 = "광합성은 빛 에너지를 화학 에너지로 바꾸는 과정이다. 엽록체에서 일어난다."
S2 = "호흡은 포도당을 분해해 에너지를 얻는 과정이다."
GOOD = "광합성은 빛 에너지를 화학 에너지로 바꾸는 과정이다."
BAD = "달의 표면은 초록색 치즈로 덮여 있다."


def raw_config(**changes) -> dict:
    raw = json.loads(Path(DEFAULT_CONFIG_PATH).read_text(encoding="utf-8"))
    raw.update(changes)
    return raw


def answer(text=GOOD, goal_ids=None, **extra) -> dict:
    return {"text": text, "goal_ids": list(GOALS) if goal_ids is None else goal_ids, **extra}


class Recorder:
    """호출 입력을 기록하는 가짜 모델 함수."""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)
        return self.reply(payload) if callable(self.reply) else self.reply


def worker(name="worker-a", family="family-a", reply=None) -> EngineSpec:
    return EngineSpec(name, family, Recorder(answer() if reply is None else reply))


def verifier(name="checker", family="family-z", supported=True) -> VerifierSpec:
    return VerifierSpec(name, family, Recorder({"supported": supported, "reasons": []}))


class Bed:
    """실제 SQLite 파일 위의 엔진 + 챗봇 핵심부."""

    def __init__(self, *, config=None, **core_kwargs):
        self.tmp = TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dreamkeeper.sqlite3"
        self.config = parse_config(config or raw_config())
        self.engine = DreamkeeperEngine(self.path, "owner-a")
        self.engine.create_task(L, T, goals=GOALS, priorities=["g1", "g2"],
                                rules=["근거 없는 문장은 내보내지 않는다"],
                                obligations={"o1": "실제 근거만 쓴다"}, user_receipt="user-1")
        self.engine.put_source(L, T, source_id="s1", fields={"body": S1, "note": "교과서 3장"},
                               expected_revision=0, user_receipt="src")
        self.engine.put_source(L, T, source_id="s2", fields={"body": S2},
                               expected_revision=0, user_receipt="src")
        self.core = DreamkeeperChatCore(self.engine, self.config, **core_kwargs)
        for sid in ("s1", "s2"):
            self.core.set_source_policy(L, T, source_id=sid, allowed_purposes=["explain", "fact"],
                                        valid=True, user_receipt="policy")

    def reopen(self, **core_kwargs) -> DreamkeeperChatCore:
        self.engine.close()
        self.engine = DreamkeeperEngine(self.path, "owner-a")
        self.core = DreamkeeperChatCore(self.engine, self.config, **core_kwargs)
        return self.core

    def close(self):
        self.engine.close()
        self.tmp.cleanup()

    def steps(self, spec, verifier_spec=None, *, purpose="explain", domain="education",
              question="광합성이 무엇인가요?"):
        """그래프 없이 핵심부 단계를 차례로 호출(단계별 시험용)."""
        core = self.core
        tid = core.start_turn(L, T, question=question, purpose=purpose, domain=domain)
        assert core.judge_purpose(L, T, tid) == purpose
        assert core.select_evidence(L, T, tid)
        binding = core.begin_attempt(L, T, tid, spec)
        core.generate(L, T, tid, binding, spec)
        return tid, binding
