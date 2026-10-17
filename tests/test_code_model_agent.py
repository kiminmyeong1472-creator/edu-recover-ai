"""Offline tool-loop contracts; no inference provider or host command runs."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import unittest
from unittest.mock import patch

from app.services import code_model_agent as agent
from app.services import open_model_transport as transport
from app.services.control_plane import ProviderHeld
from app.services.open_model_http import OpenModelHTTPError


MODEL = "DeepSeek-V4.1-Flash"
ENV = {"EDURECOVER_OPEN_MODELS_ENABLED": "true",
       "EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "true",
       "EDURECOVER_ALLOWED_PROVIDERS": "deepseek,openai,qwen",
       "DEEPSEEK_API_KEY": "private-deepseek-token"}


def tool(name="file_list", args=None, call_id="call_1"):
    return {"id": call_id, "type": "function", "function": {
        "name": name, "arguments": json.dumps({} if args is None else args)}}


def response(content="완료", calls=None, reasoning=None):
    result = {"role": "assistant", "content": content}
    if calls is not None:
        result["tool_calls"] = calls
    if reasoning is not None:
        result["reasoning_content"] = reasoning
    return result


def wire(message, finish="stop"):
    return {"choices": [{"finish_reason": finish, "message": message}]}


class Turns:
    def __init__(self, *outputs):
        self.outputs = list(outputs)
        self.calls = []

    async def __call__(self, messages, tools):
        self.calls.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        return self.outputs.pop(0)


class Executor:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def execute(self, name, arguments):
        self.calls.append((name, arguments))
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {"ok": True, "files": []}


class CodeModelAgentTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, ENV, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def run_agent(self, turn, executor=None, **kwargs):
        return asyncio.run(agent.run_code_agent(
            MODEL, "프로젝트를 수정하고 테스트하십시오.", executor or Executor(), turn=turn, **kwargs))

    def test_write_run_failure_repair_run_success_and_final_answer(self):
        write1 = tool("file_write", {"path": "sum.py", "content": "def add(a,b): return a-b\n"}, "write_1")
        run1 = tool("run_command", {"argv": ["python3", "-m", "unittest"], "timeout_seconds": 30}, "test_1")
        write2 = tool("file_write", {"path": "sum.py", "content": "def add(a,b): return a+b\n"}, "write_2")
        run2 = tool("run_command", {"argv": ["python3", "-m", "unittest"]}, "test_2")
        turns = Turns(response(None, [write1]), response(None, [run1]), response(None, [write2]),
                      response(None, [run2]), response("sum.py를 고쳤고 실행 기록에서 테스트 결과를 확인했습니다."))
        executor = Executor({"ok": True, "path": "sum.py"},
                            {"ok": True, "exit_code": 1, "stdout": "", "stderr": "FAILED (failures=1)"},
                            {"ok": True, "path": "sum.py"},
                            {"ok": True, "exit_code": 0, "stdout": "", "stderr": "Ran 1 test\nOK"})
        result = self.run_agent(turns, executor)
        self.assertEqual("completed", result["status"])
        self.assertEqual(4, len(result["steps"]))
        self.assertEqual(1, result["steps"][1]["result"]["exit_code"])
        self.assertEqual(0, result["steps"][3]["result"]["exit_code"])
        self.assertEqual("executor_results_only", result["evidence_basis"])
        self.assertEqual(4, result["executed_tool_count"])
        self.assertEqual(2, result["command_count"])
        self.assertFalse(result["system_applied"])
        self.assertNotIn("content", result["steps"][0]["arguments"])
        self.assertEqual(64, len(result["steps"][0]["arguments"]["content_sha256"]))
        failed_feedback = turns.calls[2][0][-1]
        self.assertEqual("tool", failed_feedback["role"])
        self.assertEqual(1, json.loads(failed_feedback["content"])["exit_code"])
        self.assertEqual("test_1", failed_feedback["tool_call_id"])

    def test_model_claim_is_answer_only_never_execution_evidence(self):
        executor = Executor()
        result = self.run_agent(Turns(response("모든 테스트에 통과했고 운영 서버 배포도 완료했습니다.")), executor)
        self.assertEqual([], result["steps"])
        self.assertEqual([], executor.calls)
        self.assertFalse(result["system_applied"])
        self.assertEqual(0, result["command_count"])
        self.assertNotIn("tests_passed", result)
        self.assertNotIn("deployed", result)

    def test_validate_whole_batch_before_first_file_change(self):
        executor = Executor()
        turns = Turns(response(None, [tool("file_write", {"path": "good.py", "content": "print(1)"}),
                                      tool("file_read", {"path": "../host.env"}, "bad_2")]))
        with self.assertRaises(transport.OpenModelError):
            self.run_agent(turns, executor)
        self.assertEqual([], executor.calls)

    def test_unknown_extra_and_malformed_tool_calls_fail_closed(self):
        invalid = [
            tool("deploy", {}), tool("file_read", {"path": "/etc/passwd"}),
            tool("file_read", {"path": "C:\\secret"}),
            tool("file_read", {"path": "."}), tool("file_list", {"path": "a//b"}),
            tool("file_write", {"path": "a.py", "content": "x" * (agent.MAX_FILE_BYTES + 1)}),
            tool("file_write", {"path": "a.py", "content": "x", "execute": True}),
            tool("run_command", {"argv": "python3 -c 'print(1)'"}),
            tool("run_command", {"argv": ["python3"], "timeout_seconds": True}),
            tool("run_command", {"argv": ["python3"], "timeout_seconds": 121}),
            tool("run_command", {"argv": ["python3"], "cwd": "../production"}),
            {**tool(), "index": 0}, {**tool(), "type": "unknown"},
            {**tool(), "id": "../bad"},
            {**tool(), "function": {**tool()["function"], "extra": "bad"}},
            {**tool(), "function": {"name": "file_list", "arguments": '{"path":".","path":"a"}'}},
            {**tool(), "function": {"name": "file_list", "arguments": '{"path":NaN}'}},
        ]
        for call in invalid:
            with self.subTest(call=call):
                executor = Executor()
                with self.assertRaises(transport.OpenModelError):
                    self.run_agent(Turns(response(None, [call])), executor)
                self.assertEqual([], executor.calls)

    def test_duplicate_ids_within_and_across_turns_never_repeat_side_effect(self):
        for turns in (Turns(response(None, [tool(), tool()])),
                      Turns(response(None, [tool()]), response(None, [tool()]))):
            with self.subTest(turns=turns):
                executor = Executor()
                with self.assertRaises(transport.OpenModelError):
                    self.run_agent(turns, executor)
                self.assertLessEqual(len(executor.calls), 1)

    def test_round_limit_is_explicit_not_success(self):
        executor = Executor()
        result = self.run_agent(Turns(response(None, [tool()])), executor, max_rounds=1)
        self.assertEqual("round_limit_reached", result["status"])
        self.assertEqual(1, len(executor.calls))

    def test_tool_limit_rejects_whole_next_batch(self):
        executor = Executor()
        result = self.run_agent(Turns(response(None, [tool(call_id="one"), tool(call_id="two")])),
                                executor, max_tool_calls=1)
        self.assertEqual("tool_limit_reached", result["status"])
        self.assertEqual([], executor.calls)
        self.assertEqual([], result["steps"])

    def test_executor_exception_aborts_without_leaking_exception_or_model_retry(self):
        turns = Turns(response(None, [tool()]), response("가짜 성공"))
        executor = Executor(RuntimeError("secret-token: the whole credential"))
        result = self.run_agent(turns, executor)
        self.assertEqual("executor_failed", result["status"])
        self.assertEqual(1, len(turns.calls))
        self.assertNotIn("secret-token", json.dumps(result))
        self.assertEqual("CODE_AGENT_EXECUTOR_FAILED", result["steps"][0]["result"]["code"])
        self.assertEqual(0, result["executed_tool_count"])

    def test_cancel_before_first_turn_never_calls_provider_or_executor(self):
        turns, executor = Turns(response("unused")), Executor()
        result = self.run_agent(turns, executor, cancel_check=lambda: True)
        self.assertEqual("cancelled", result["status"])
        self.assertEqual([], turns.calls)
        self.assertEqual([], executor.calls)

    def test_cancel_during_provider_call_stops_before_tool_execution(self):
        cancelled = False
        def turn(messages, tools):
            nonlocal cancelled
            cancelled = True
            return response(None, [tool()])
        executor = Executor()
        result = self.run_agent(turn, executor, cancel_check=lambda: cancelled)
        self.assertEqual("cancelled", result["status"])
        self.assertEqual([], executor.calls)

    def test_cancel_after_first_tool_preserves_its_evidence_and_skips_rest(self):
        cancelled = False
        class CancelExecutor(Executor):
            def execute(self, name, args):
                nonlocal cancelled
                result = super().execute(name, args)
                cancelled = True
                return result
        executor = CancelExecutor()
        turns = Turns(response(None, [tool(call_id="one"), tool(call_id="two")]), response("unused"))
        result = self.run_agent(turns, executor, cancel_check=lambda: cancelled)
        self.assertEqual("cancelled", result["status"])
        self.assertEqual(1, len(executor.calls))
        self.assertEqual(1, len(result["steps"]))
        self.assertEqual(1, len(turns.calls))

    def test_invalid_or_oversized_executor_results_abort(self):
        for invalid in ("not an object", {"bad": float("nan")}, {"stdout": "x" * (agent.MAX_RESULT_BYTES + 1)}):
            with self.subTest(invalid_type=type(invalid)):
                result = self.run_agent(Turns(response(None, [tool()])), Executor(invalid))
                self.assertEqual("executor_failed", result["status"])

    def test_private_reasoning_only_returns_to_same_model_not_result(self):
        turns = Turns(response(None, [tool()], "private reasoning text"), response("최종 답변"))
        result = self.run_agent(turns)
        self.assertEqual("private reasoning text", turns.calls[1][0][-2]["reasoning_content"])
        self.assertNotIn("private reasoning text", json.dumps(result))

    def test_global_hold_stops_even_injected_model_and_tools(self):
        turns, executor = Turns(response("unused")), Executor()
        with patch.dict(os.environ, {"EDURECOVER_AI_HOLD": "true"}):
            with self.assertRaises(ProviderHeld):
                self.run_agent(turns, executor)
        self.assertEqual([], turns.calls)
        self.assertEqual([], executor.calls)

    def test_hold_enabled_by_model_turn_stops_before_tool(self):
        def turn(messages, tools):
            os.environ["EDURECOVER_AI_HOLD"] = "true"
            return response(None, [tool()])
        executor = Executor()
        with self.assertRaises(ProviderHeld):
            self.run_agent(turn, executor)
        self.assertEqual([], executor.calls)

    def test_schema_is_fresh_and_contains_only_available_tools(self):
        schemas = agent.tool_schemas()
        self.assertEqual({"file_read", "file_write", "file_list", "run_command"},
                         {row["function"]["name"] for row in schemas})
        schemas[0]["function"]["parameters"]["properties"].clear()
        self.assertIn("path", agent.tool_schemas()[0]["function"]["parameters"]["properties"])

    def test_production_uses_exact_deepseek_binding_without_max_token_cap(self):
        with patch.object(transport, "post_json", return_value=wire(response("코드 설명"))) as post:
            result = asyncio.run(agent.run_code_agent(MODEL, "코드", Executor()))
        endpoint, payload, token = post.call_args.args
        self.assertEqual(transport.DEEPSEEK_CHAT_ENDPOINT, endpoint)
        self.assertEqual("deepseek-flash", payload["model"])
        self.assertEqual("private-deepseek-token", token)
        self.assertEqual("auto", payload["tool_choice"])
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn(token, json.dumps(result))
        self.assertEqual(1, post.call_count)

    def test_preflight_rejects_missing_credentials_before_any_model_or_executor(self):
        with patch.dict(os.environ, {**ENV, "DEEPSEEK_API_KEY": ""}, clear=True), \
                patch.object(transport, "post_json") as post:
            with self.assertRaises(transport.OpenModelError) as caught:
                agent.configured_turn(MODEL, route="learning")
            self.assertEqual("OPEN_MODEL_CREDENTIALS_UNAVAILABLE", caught.exception.code)
            post.assert_not_called()

    def test_openrouter_preserves_zdr_and_luna_azure_only(self):
        env = {"EDURECOVER_GPT6_LUNA_AZURE_ENABLED": "true", "OPENROUTER_API_KEY": "private-router-token"}
        with patch.dict(os.environ, env), patch.object(transport, "post_json", return_value=wire(response("답변"))) as post:
            asyncio.run(agent.run_code_agent("GPT-6-Luna", "코드", Executor()))
        self.assertEqual({"zdr": True, "data_collection": "deny", "only": ["azure"], "allow_fallbacks": False},
                         post.call_args.args[1]["provider"])

    def test_chutes_uses_dedicated_key_without_fallback(self):
        with patch.dict(os.environ, {"CHUTES_API_KEY": "private-chutes-token"}), \
                patch.object(transport, "post_json", return_value=wire(response("답변"))) as post:
            asyncio.run(agent.run_code_agent("DeepSeek-V3.2", "코드", Executor(), route="chutes"))
        endpoint, payload, token = post.call_args.args
        self.assertEqual("https://llm.chutes.ai/v1/chat/completions", endpoint)
        self.assertEqual("deepseek-ai/DeepSeek-V3.2-TEE", payload["model"])
        self.assertEqual("private-chutes-token", token)
        with patch.object(transport, "post_json") as post:
            with self.assertRaises(transport.OpenModelError) as caught:
                asyncio.run(agent.run_code_agent("DeepSeek-V3.2", "코드", Executor(), route="chutes"))
            self.assertEqual("OPEN_MODEL_CREDENTIALS_UNAVAILABLE", caught.exception.code)
            post.assert_not_called()

    def test_bedrock_not_silently_replaced_by_http_route(self):
        binding = {"transport": "bedrock", "region": "us-east-1", "model_id": "owner-model"}
        with patch.dict(os.environ, {"EDURECOVER_OPEN_MODEL_BINDINGS_JSON": json.dumps({"Qwen3.6-27B": binding})}), \
                patch.object(transport, "post_json") as post:
            with self.assertRaises(transport.OpenModelError) as caught:
                asyncio.run(agent.run_code_agent("Qwen3.6-27B", "코드", Executor()))
            self.assertEqual("CODE_AGENT_TOOLS_UNSUPPORTED", caught.exception.code)
            post.assert_not_called()

    def test_configuration_change_between_turns_aborts(self):
        turn = agent.configured_turn(MODEL)
        with patch.dict(os.environ, {"EDURECOVER_DEEPSEEK_DIRECT_ENABLED": "false"}), \
                patch.object(transport, "post_json") as post:
            with self.assertRaises(transport.OpenModelError) as caught:
                asyncio.run(turn([], []))
            self.assertEqual("CODE_AGENT_ROUTE_CHANGED", caught.exception.code)
            post.assert_not_called()

    def test_http_failure_does_not_retry_or_return_upstream_text(self):
        with patch.object(transport, "post_json", side_effect=OpenModelHTTPError("OPEN_MODEL_INSUFFICIENT_BALANCE")) as post:
            with self.assertRaises(transport.OpenModelError) as caught:
                asyncio.run(agent.run_code_agent(MODEL, "코드", Executor()))
        self.assertEqual(402, caught.exception.http_status)
        self.assertEqual(1, post.call_count)

    def test_malformed_provider_finish_never_executes_tools(self):
        malformed = [wire(response(None, [tool()]), "stop"),
                     wire(response("text"), "tool_calls"), wire(response("text"), "length"),
                     {"choices": []}, wire(response("text"), []), response(None), response("", []),
                     {"role": "user", "content": "fake"}]
        for raw in malformed:
            with self.subTest(raw=raw):
                executor = Executor()
                with self.assertRaises(transport.OpenModelError):
                    self.run_agent(Turns(raw), executor)
                self.assertEqual([], executor.calls)


if __name__ == "__main__":
    unittest.main()
