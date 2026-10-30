"""Offline SDK doubles: no package installation, CLI process, or paid requests."""
import asyncio
from dataclasses import dataclass, field
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services import dreamkeeper_claude_sdk as adapter_module
from app.services.dreamkeeper_claude_sdk import (
    DreamkeeperClaudeSDKAdapter, DreamkeeperSDKError,
    DreamkeeperSDKUnavailable, MODEL_TOOLS, READ_TOOL, SUBMIT_TOOL,
)


@dataclass
class FakeSystemMessage:
    subtype: str
    data: dict


@dataclass
class FakeAssistantMessage:
    content: list = field(default_factory=list)
    error: str | None = None


@dataclass
class FakeToolUseBlock:
    name: str


@dataclass
class FakeResultMessage:
    subtype: str = "success"
    is_error: bool = False
    permission_denials: list = field(default_factory=list)
    terminal_reason: str | None = "completed"
    result: str = "UNREVIEWED RAW SDK OUTPUT"


class FakeSDK:
    SystemMessage = FakeSystemMessage
    AssistantMessage = FakeAssistantMessage
    ToolUseBlock = FakeToolUseBlock
    ResultMessage = FakeResultMessage
    HookMatcher = SimpleNamespace

    def __init__(self, scenario=None, *, inventory=None, result=None):
        self.scenario = scenario
        self.inventory = list(MODEL_TOOLS) if inventory is None else inventory
        self.result = result or FakeResultMessage()
        self.options = None
        self.queries = []
        self.exited = False
        self.handlers = {}
        self.outputs = []

    def tool(self, name, description, schema):
        def decorate(handler):
            return SimpleNamespace(name=name, description=description,
                                   input_schema=schema, handler=handler)
        return decorate

    def create_sdk_mcp_server(self, *, name, version, tools):
        self.handlers = {f"mcp__{name}__{tool.name}": tool.handler for tool in tools}
        return {"type": "sdk", "name": name, "version": version, "tools": tools}

    def ClaudeAgentOptions(self, **kwargs):
        self.options = SimpleNamespace(**kwargs)
        return self.options

    def ClaudeSDKClient(self, *, options):
        sdk = self

        class Client:
            async def __aenter__(self):
                assert Path(options.cwd).is_dir()
                return self

            async def __aexit__(self, *_args):
                sdk.exited = True

            async def query(self, prompt):
                sdk.queries.append(prompt)

            async def receive_response(self):
                yield FakeSystemMessage("init", {"tools": sdk.inventory})
                if sdk.scenario:
                    await sdk.scenario(sdk)
                yield FakeAssistantMessage([SimpleNamespace(text="UNREVIEWED RAW SDK OUTPUT")])
                yield sdk.result

        return Client()

    async def invoke(self, name, args):
        for matcher in self.options.hooks["PreToolUse"]:
            for hook in matcher.hooks:
                decision = await hook({"tool_name": name, "tool_input": args}, None, {})
                if decision.get("hookSpecificOutput", {}).get("permissionDecision") == "deny":
                    self.outputs.append(decision)
                    return decision
        result = await self.handlers[name](args)
        self.outputs.append(result)
        return result


class FakePort:
    def __init__(self):
        self.active = True
        self.submitted = []
        self.deliveries = []

    def read_contract(self, source_selection=None, *, record_delivery=True):
        if not self.active:
            raise ValueError("stale/held run")
        if record_delivery:
            self.deliveries.append(source_selection)
        return {"goals": [{"id": "g1", "text": "score essay"}],
                "priorities": ["g1"], "rules": ["no copying"],
                "obligations": ["verify score"], "goal_revision": 1,
                "run_epoch": 1, "sources": {"essay": {"text": {"revision": 1}}}}

    def submit_candidate(self, **kwargs):
        self.read_contract(record_delivery=False)
        self.submitted.append(kwargs)
        return f"c{len(self.submitted)}"


class FakeEngine:
    def __init__(self):
        self.port = FakePort()
        self.consumed = []

    def model_port(self, _binding):
        return self.port

    def consume(self, logical_id, task_id, *, candidate_id, approval_id, purpose):
        self.port.read_contract(record_delivery=False)
        if approval_id != "host-review":
            raise ValueError("approval missing")
        self.consumed.append((logical_id, task_id, candidate_id, approval_id, purpose))
        return {"status": "APPROVED", "content": self.port.submitted[0]["content"]}


def candidate_args():
    return {"content": {"score": 4, "novel_hypothesis": "possible alternative"},
            "purpose": "essay_feedback", "source_refs": {"essay": {"text": 1}},
            "consumption_complete": True, "goal_ids": ["g1"]}


async def submit_once(sdk):
    await sdk.invoke(READ_TOOL, {})
    await sdk.invoke(SUBMIT_TOOL, candidate_args())


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = FakeEngine()
        self.binding = SimpleNamespace(model_id="explicit-model", logical_id="dreamkeeper",
                                       task_id="essay-1", run_id="run-1")
        # Any accidental Python network path fails this entire test fixture.
        self.network = patch("socket.create_connection", side_effect=AssertionError("network forbidden"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def adapter(self, sdk, **kwargs):
        return DreamkeeperClaudeSDKAdapter(self.engine, model="explicit-model", sdk=sdk, **kwargs)

    async def test_mcp_engine_state_and_candidate_receipt_without_raw_final(self):
        sdk = FakeSDK(submit_once)
        result = await self.adapter(sdk).run(self.binding, "grade this essay")
        self.assertEqual(result.status, "DRAFT")
        self.assertEqual(result.candidate_ids, ("c1",))
        self.assertNotIn("UNREVIEWED", repr(result))
        self.assertEqual(self.engine.port.submitted, [candidate_args()])
        self.assertEqual(self.engine.consumed, [])
        context = json.loads(sdk.outputs[0]["content"][0]["text"])
        self.assertEqual(context["obligations"], ["verify score"])
        self.assertEqual(set(sdk.handlers), MODEL_TOOLS)
        self.assertEqual(self.engine.port.deliveries, [None])
        self.assertTrue(sdk.exited)

    async def test_host_field_selection_and_delivery_tracking_are_not_model_arguments(self):
        sdk = FakeSDK(submit_once)
        selection = {"essay": ["text"]}
        adapter = self.adapter(sdk, source_selection=selection)
        selection["essay"].append("private_notes")
        await adapter.run(self.binding, "work")
        self.assertEqual(self.engine.port.deliveries, [{"essay": ["text"]}])
        async def tamper(sdk):
            result = await sdk.invoke(READ_TOOL, {"record_delivery": False})
            self.assertTrue(result["isError"])
        with self.assertRaises(DreamkeeperSDKError):
            await self.adapter(FakeSDK(tamper)).run(self.binding, "work")

    async def test_model_must_read_context_before_submitting(self):
        async def scenario(sdk):
            await sdk.invoke(SUBMIT_TOOL, candidate_args())
        with self.assertRaises(DreamkeeperSDKError):
            await self.adapter(FakeSDK(scenario)).run(self.binding, "work")
        self.assertEqual(self.engine.port.submitted, [])

    async def test_configuration_removes_builtins_and_external_settings(self):
        sdk = FakeSDK(submit_once)
        await self.adapter(sdk).run(self.binding, "work")
        options = sdk.options
        self.assertEqual(options.tools, [])
        self.assertEqual(set(options.allowed_tools), MODEL_TOOLS)
        self.assertTrue(options.strict_mcp_config)
        for field_name in ("setting_sources", "skills", "plugins", "add_dirs"):
            self.assertEqual(getattr(options, field_name), [])
        self.assertEqual(options.agents, {})
        self.assertEqual(set(options.mcp_servers), {"dreamkeeper"})
        self.assertEqual(options.permission_mode, "dontAsk")
        self.assertIsNone(options.fallback_model)
        self.assertIsNone(options.resume)
        self.assertFalse(options.continue_conversation)
        self.assertFalse(Path(options.cwd).exists())

    async def test_extra_tools_are_denied_even_after_valid_candidate(self):
        for tool in ("Bash", "Read", "Skill", "Agent", "mcp__other__approve",
                     "mcp__dreamkeeper__release_obligation", "mcp__dreamkeeper__revise_task"):
            with self.subTest(tool=tool):
                async def scenario(sdk):
                    await submit_once(sdk)
                    denied = await sdk.invoke(tool, {})
                    self.assertFalse(denied["continue_"])
                sdk = FakeSDK(scenario)
                with self.assertRaisesRegex(DreamkeeperSDKError, "Additional tool"):
                    await self.adapter(sdk).run(self.binding, "work")
                self.assertTrue(sdk.exited)
                self.assertEqual(self.engine.consumed, [])

    async def test_unknown_inventory_stops_before_tool_execution(self):
        sdk = FakeSDK(submit_once, inventory=[*MODEL_TOOLS, "ToolSearch"])
        with self.assertRaisesRegex(DreamkeeperSDKError, "inventory"):
            await self.adapter(sdk).run(self.binding, "work")
        self.assertEqual(self.engine.port.submitted, [])
        self.assertTrue(sdk.exited)

    async def test_candidate_cannot_submit_approval_or_goal_change_fields(self):
        async def scenario(sdk):
            args = candidate_args()
            args["checks"] = {"all_passed": True}
            result = await sdk.invoke(SUBMIT_TOOL, args)
            self.assertTrue(result["isError"])
        sdk = FakeSDK(scenario)
        with self.assertRaisesRegex(DreamkeeperSDKError, "Candidate rejected"):
            await self.adapter(sdk).run(self.binding, "work")
        self.assertEqual(self.engine.port.submitted, [])

    async def test_changed_contract_stops_before_candidate(self):
        async def scenario(sdk):
            await sdk.invoke(READ_TOOL, {})
            self.engine.port.active = False
            await sdk.invoke(SUBMIT_TOOL, candidate_args())
        with self.assertRaises(DreamkeeperSDKError):
            await self.adapter(FakeSDK(scenario)).run(self.binding, "work")
        self.assertEqual(self.engine.port.submitted, [])

    async def test_change_during_completion_cannot_return_stale_draft(self):
        async def scenario(sdk):
            await submit_once(sdk)
            self.engine.port.active = False
        with self.assertRaises(DreamkeeperSDKError):
            await self.adapter(FakeSDK(scenario)).run(self.binding, "work")

    async def test_raw_success_without_candidate_is_not_a_result(self):
        with self.assertRaisesRegex(DreamkeeperSDKError, "No completed DRAFT"):
            await self.adapter(FakeSDK()).run(self.binding, "work")

    async def test_sdk_error_is_not_a_result(self):
        sdk = FakeSDK(submit_once, result=FakeResultMessage(subtype="error_max_turns", is_error=True))
        with self.assertRaisesRegex(DreamkeeperSDKError, "did not complete"):
            await self.adapter(sdk).run(self.binding, "work")
        self.assertEqual(len(sdk.queries), 1)
        self.assertTrue(sdk.exited)

    async def test_timeout_closes_client_without_fallback(self):
        async def stall(_sdk):
            await asyncio.Event().wait()
        sdk = FakeSDK(stall)
        with self.assertRaises(DreamkeeperSDKError):
            await self.adapter(sdk, timeout_seconds=0.01).run(self.binding, "work")
        self.assertEqual(len(sdk.queries), 1)
        self.assertTrue(sdk.exited)

    async def test_host_consumption_requires_engine_approval(self):
        adapter = self.adapter(FakeSDK(submit_once))
        draft = await adapter.run(self.binding, "work")
        with self.assertRaisesRegex(ValueError, "approval"):
            adapter.consume_final(self.binding, candidate_id=draft.candidate_ids[0],
                                  approval_id="model-says-approved", purpose="essay_feedback")
        result = adapter.consume_final(self.binding, candidate_id=draft.candidate_ids[0],
                                       approval_id="host-review", purpose="essay_feedback")
        self.assertEqual(result["status"], "APPROVED")
        self.assertEqual(len(self.engine.consumed), 1)

    async def test_mismatched_model_stops_without_starting_sdk(self):
        sdk = FakeSDK(submit_once)
        self.binding.model_id = "other-model"
        with self.assertRaisesRegex(DreamkeeperSDKError, "model"):
            await self.adapter(sdk).run(self.binding, "work")
        self.assertIsNone(sdk.options)

    async def test_real_engine_tracks_only_delivered_fields_and_requires_host_trace_review(self):
        from app.services.dreamkeeper_engine import DreamkeeperEngine, EngineHold
        engine = DreamkeeperEngine(":memory:", "owner")
        self.addCleanup(engine.close)
        engine.create_task("dreamkeeper", "essay-1", goals={"g1": "score essay"},
                           priorities=["g1"], rules=["no copying"],
                           obligations={"o1": "verify score"}, user_receipt="user-task")
        engine.put_source("dreamkeeper", "essay-1", source_id="essay",
                          fields={"text": "student essay", "private_notes": "not for model"},
                          expected_revision=0, user_receipt="user-source")
        binding = engine.begin_run("dreamkeeper", "essay-1", model_id="explicit-model",
                                   session_id="session-1")

        async def omit_refs(sdk):
            result = await sdk.invoke(READ_TOOL, {})
            context = json.loads(result["content"][0]["text"])
            self.assertEqual(set(context["sources"]["essay"]["fields"]), {"text"})
            args = candidate_args()
            args["source_refs"] = {}  # Model omission cannot erase actual delivery.
            await sdk.invoke(SUBMIT_TOOL, args)

        adapter = DreamkeeperClaudeSDKAdapter(
            engine, model="explicit-model", sdk=FakeSDK(omit_refs),
            source_selection={"essay": ["text"]})
        draft = await adapter.run(binding, "score the authorized essay text")
        cid = draft.candidate_ids[0]
        candidate = engine.candidate("dreamkeeper", "essay-1", cid)
        self.assertEqual(candidate["source_refs"], {"essay": {"text": 1}})
        self.assertFalse(candidate["source_trace_verified"])
        checks = {name: True for name in engine.REVIEW_CHECKS if name != "source_trace_verified"}
        with self.assertRaises(EngineHold):
            engine.review_candidate("dreamkeeper", "essay-1", candidate_id=cid,
                                    checks=checks, reviewer="host-reviewer", receipt="review-1",
                                    obligation_checks={"o1": True})
        checks["source_trace_verified"] = True
        with self.assertRaises(EngineHold):
            engine.review_candidate("dreamkeeper", "essay-1", candidate_id=cid,
                                    checks=checks, reviewer="host-reviewer", receipt="missing-obligation")
        approval = engine.review_candidate("dreamkeeper", "essay-1", candidate_id=cid,
                                           checks=checks, reviewer="host-reviewer", receipt="review-2",
                                           obligation_checks={"o1": True})
        result = adapter.consume_final(binding, candidate_id=cid, approval_id=approval,
                                       purpose="essay_feedback")
        self.assertEqual(result["content"], candidate_args()["content"])
        with self.assertRaises(EngineHold):
            adapter.consume_final(binding, candidate_id=cid, approval_id=approval,
                                  purpose="essay_feedback")

    async def test_sdk_is_lazy_and_missing_or_changed_sdk_stops(self):
        with patch.object(adapter_module, "import_module") as imported:
            normal = DreamkeeperClaudeSDKAdapter(self.engine, model="explicit-model")
            imported.assert_not_called()
            with patch.object(adapter_module.metadata, "version", return_value="0.1.59"):
                with self.assertRaises(DreamkeeperSDKUnavailable):
                    await normal.run(self.binding, "work")
            imported.assert_not_called()
            with patch.object(adapter_module.metadata, "version",
                              side_effect=adapter_module.metadata.PackageNotFoundError):
                with self.assertRaises(DreamkeeperSDKUnavailable):
                    await normal.run(self.binding, "work")
            imported.assert_not_called()


if __name__ == "__main__":
    unittest.main()
