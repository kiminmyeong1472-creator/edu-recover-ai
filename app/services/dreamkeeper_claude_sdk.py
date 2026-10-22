"""Attach the local logical engine to the optional Python Agent SDK.

The model receives two in-process MCP tools, not engine administration rights.
SDK prose is never a final answer: ``run`` returns DRAFT candidate IDs and only
the host's ``consume_final`` delegates to the engine's reviewed consumption path.
This is runtime integration; it does not change any provider model's weights.

API checked against the official 0.2.170 sources on 2026-10-22:
https://github.com/anthropics/claude-agent-sdk-python/blob/main/README.md
https://github.com/anthropics/claude-agent-sdk-python/blob/main/pyproject.toml
https://code.claude.com/docs/en/agent-sdk/python
https://code.claude.com/docs/en/agent-sdk/permissions

``allowed_tools`` only auto-approves tools. Availability is separately restricted
with tools=[], explicit MCP configuration, and a PreToolUse deny hook. Disabling
filesystem settings is not an OS sandbox and does not suppress managed policy or
the subprocess's inherited environment. No SDK is imported until a run starts.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from importlib import import_module, metadata
import json
import math
from tempfile import TemporaryDirectory
from typing import Any, Literal, Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.dreamkeeper_engine import RunBinding


SDK_REQUIRED_VERSION = "0.2.170"
SERVER_NAME = "dreamkeeper"
READ_TOOL = f"mcp__{SERVER_NAME}__read_context"
SUBMIT_TOOL = f"mcp__{SERVER_NAME}__submit_candidate"
MODEL_TOOLS = frozenset({READ_TOOL, SUBMIT_TOOL})


class DreamkeeperSDKError(RuntimeError):
    """The SDK run stopped; no SDK text was promoted to a final result."""


class DreamkeeperSDKUnavailable(DreamkeeperSDKError):
    """The reviewed optional SDK dependency is missing or incompatible."""


class _ModelPort(Protocol):
    def read_contract(self, source_selection: dict[str, list[str]] | None = None,
                      *, record_delivery: bool = True) -> dict[str, Any]: ...

    def submit_candidate(self, *, content: Any, purpose: str,
                         source_refs: dict[str, dict[str, int]],
                         consumption_complete: bool, goal_ids: list[str]) -> str: ...


class _Engine(Protocol):
    def model_port(self, binding: RunBinding) -> _ModelPort: ...

    def consume(self, logical_id: str, task_id: str, *, candidate_id: str,
                approval_id: str, purpose: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class DraftRun:
    """Candidate receipts only; deliberately contains no model answer text."""

    candidate_ids: tuple[str, ...]
    logical_id: str
    task_id: str
    run_id: str
    status: Literal["DRAFT"] = "DRAFT"


def _load_sdk() -> Any:
    try:
        version = metadata.version("claude-agent-sdk")
        if version != SDK_REQUIRED_VERSION:
            raise DreamkeeperSDKUnavailable(
                f"Reviewed claude-agent-sdk=={SDK_REQUIRED_VERSION} is required; "
                "review another version before using it."
            )
        sdk = import_module("claude_agent_sdk")
    except (ImportError, metadata.PackageNotFoundError) as exc:
        raise DreamkeeperSDKUnavailable(
            f"Optional claude-agent-sdk=={SDK_REQUIRED_VERSION} is not installed."
        ) from exc
    return sdk


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _tool_result(value: Any, *, error: bool = False) -> dict[str, Any]:
    result: dict[str, Any] = {"content": [{"type": "text", "text": _json_text(value)}]}
    if error:
        result["isError"] = True
    return result


_SUBMIT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "content": {},
        "purpose": {"type": "string", "minLength": 1},
        "source_refs": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": {"type": "integer", "minimum": 1},
            },
        },
        "consumption_complete": {"type": "boolean"},
        "goal_ids": {"type": "array", "items": {"type": "string", "minLength": 1}},
    },
    "required": ["content", "purpose", "source_refs", "consumption_complete", "goal_ids"],
}


def _candidate_args(args: Any) -> dict[str, Any]:
    if not isinstance(args, dict) or set(args) != set(_SUBMIT_SCHEMA["required"]):
        raise ValueError("Only candidate fields are accepted")
    value = json.loads(_json_text(args))  # Detach mutable model data; reject NaN.
    if not isinstance(value["purpose"], str) or not value["purpose"].strip():
        raise ValueError("A purpose is required")
    if type(value["consumption_complete"]) is not bool:
        raise ValueError("A boolean consumption declaration is required")
    if not isinstance(value["goal_ids"], list) or not all(
        isinstance(item, str) and item.strip() for item in value["goal_ids"]
    ):
        raise ValueError("Goal IDs must be strings")
    refs = value["source_refs"]
    if not isinstance(refs, dict) or not all(
        isinstance(sid, str) and sid and isinstance(fields, dict) and all(
            isinstance(field, str) and field and type(revision) is int and revision > 0
            for field, revision in fields.items()
        ) for sid, fields in refs.items()
    ):
        raise ValueError("Source fields need positive integer revisions")
    return value


class DreamkeeperClaudeSDKAdapter:
    """Host adapter; only the bound model port is registered with the SDK.

    ``sdk`` is dependency injection for offline tests. Production normally leaves
    it unset, enforcing the reviewed package version. Do not inject untrusted SDKs.
    Every run requires a binding already issued by host ``engine.begin_run``.

    ``source_selection`` is a host-controlled field allowlist. None sends all
    source fields registered for the bound task; the host must have authorized
    those fields for this model before running. Use an explicit selection to
    minimize model input. A free-text prompt can contain additional information;
    the host's source_trace_verified review must cover that input as well.
    Model-declared source_refs/consumption_complete are never proof of complete
    provenance or semantic correctness. No independent reviewer is implemented
    here, and SDK text cannot approve its own candidate.
    """

    def __init__(self, engine: _Engine, *, model: str, max_turns: int = 8,
                 max_budget_usd: float = 1.0, timeout_seconds: float = 120.0,
                 source_selection: dict[str, list[str]] | None = None,
                 sdk: Any = None):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("An explicit model is required")
        if type(max_turns) is not int or not 1 <= max_turns <= 100:
            raise ValueError("max_turns must be between 1 and 100")
        for name, value in (("max_budget_usd", max_budget_usd),
                            ("timeout_seconds", timeout_seconds)):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        self._engine = engine
        self._model = model
        self._max_turns = max_turns
        self._max_budget = max_budget_usd
        self._timeout = timeout_seconds
        if source_selection is not None and (
            not isinstance(source_selection, dict) or not all(
                isinstance(source, str) and source and isinstance(fields, list)
                and all(isinstance(item, str) and item for item in fields)
                for source, fields in source_selection.items()
            )
        ):
            raise ValueError("Source selection must map source IDs to field-name lists")
        self._source_selection = (
            json.loads(_json_text(source_selection)) if source_selection is not None else None
        )
        self._sdk = sdk

    async def run(self, binding: RunBinding, prompt: str) -> DraftRun:
        """Generate stored candidates, with no review/approval tool for the model."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("A nonempty prompt is required")
        if binding.model_id != self._model:
            raise DreamkeeperSDKError("Binding model does not match SDK configuration")
        port = self._engine.model_port(binding)
        port.read_contract(record_delivery=False)  # Host check; not model delivery.
        sdk = self._sdk if self._sdk is not None else _load_sdk()
        for attribute in ("tool", "create_sdk_mcp_server", "ClaudeAgentOptions",
                          "ClaudeSDKClient", "HookMatcher", "ResultMessage",
                          "AssistantMessage", "ToolUseBlock", "SystemMessage"):
            if not hasattr(sdk, attribute):
                raise DreamkeeperSDKUnavailable(f"Required SDK API missing: {attribute}")

        candidates: list[str] = []
        failures: list[str] = []
        context_delivered = False

        def fail(reason: str) -> None:
            if not failures:
                failures.append(reason)

        async def read_context(args: dict[str, Any]) -> dict[str, Any]:
            nonlocal context_delivered
            try:
                if failures or args != {}:
                    raise ValueError("Run stopped or unexpected context arguments")
                # Only this call delivers source fields to the model. Selection
                # is host-controlled; the model cannot suppress delivery tracking.
                result = _tool_result(port.read_contract(self._source_selection, record_delivery=True))
                context_delivered = True
                return result
            except Exception:
                fail("Context read rejected by the engine")
                return _tool_result({"status": "STOPPED", "reason": failures[0]}, error=True)

        async def submit_candidate(args: dict[str, Any]) -> dict[str, Any]:
            try:
                if failures:
                    raise ValueError("Run already stopped")
                if not context_delivered:
                    raise ValueError("Read the current context before submitting")
                port.read_contract(record_delivery=False)
                # These are model declarations, not independently verified trace
                # completeness. The engine merges delivered fields and requires
                # the host review's source_trace_verified before consumption.
                candidate_id = port.submit_candidate(**_candidate_args(args))
                if not isinstance(candidate_id, str) or not candidate_id:
                    raise ValueError("Invalid engine receipt")
                candidates.append(candidate_id)
                return _tool_result({"status": "DRAFT", "candidate_id": candidate_id})
            except Exception:
                fail("Candidate rejected by the engine")
                return _tool_result({"status": "STOPPED", "reason": failures[0]}, error=True)

        async def guard_tool(data: dict[str, Any], _tool_use_id: Any,
                             _context: Any) -> dict[str, Any]:
            if data.get("tool_name") not in MODEL_TOOLS:
                fail("Additional tool is not permitted")
            try:
                if not failures:
                    port.read_contract(record_delivery=False)
            except Exception:
                fail("Current engine contract no longer permits this run")
            if failures:
                return {
                    "continue_": False,
                    "stopReason": failures[0],
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": failures[0],
                    },
                }
            return {}

        read_tool = sdk.tool("read_context", "Read this run's current goals, rules and sources.",
                             {"type": "object", "properties": {}, "additionalProperties": False})(read_context)
        submit_tool = sdk.tool("submit_candidate", "Store a DRAFT candidate; this does not approve its use.",
                               _SUBMIT_SCHEMA)(submit_candidate)
        server = sdk.create_sdk_mcp_server(name=SERVER_NAME, version="1.0.0",
                                          tools=[read_tool, submit_tool])

        async def receive(client: Any) -> None:
            completed = False
            async for message in client.receive_response():
                if failures:
                    raise DreamkeeperSDKError(failures[0])
                if isinstance(message, sdk.SystemMessage) and message.subtype == "init":
                    available = message.data.get("tools")
                    if not isinstance(available, list) or any(
                        name not in MODEL_TOOLS for name in available
                    ):
                        raise DreamkeeperSDKError("SDK reported an unexpected tool inventory")
                if isinstance(message, sdk.AssistantMessage):
                    if getattr(message, "error", None):
                        raise DreamkeeperSDKError("SDK assistant reported an error")
                    for block in message.content:
                        if isinstance(block, sdk.ToolUseBlock) and block.name not in MODEL_TOOLS:
                            raise DreamkeeperSDKError("SDK emitted an unpermitted tool request")
                if isinstance(message, sdk.ResultMessage):
                    if (message.is_error or message.subtype != "success"
                            or getattr(message, "permission_denials", None)
                            or getattr(message, "terminal_reason", None) not in (None, "completed")):
                        raise DreamkeeperSDKError("SDK run did not complete successfully")
                    completed = True
            if failures or not completed or not candidates:
                raise DreamkeeperSDKError(failures[0] if failures else "No completed DRAFT candidate run")
            port.read_contract(record_delivery=False)  # Reject a late revision/hold.

        # This controls SDK inputs/tool availability, not OS or credential isolation.
        with TemporaryDirectory(prefix="dreamkeeper-sdk-") as workdir:
            try:
                options = sdk.ClaudeAgentOptions(
                    model=self._model, fallback_model=None,
                    tools=[], allowed_tools=sorted(MODEL_TOOLS),
                    mcp_servers={SERVER_NAME: server}, strict_mcp_config=True,
                    setting_sources=[], skills=[], plugins=[], agents={}, add_dirs=[],
                    permission_mode="dontAsk", continue_conversation=False, resume=None,
                    cwd=workdir, max_turns=self._max_turns, max_budget_usd=self._max_budget,
                    hooks={"PreToolUse": [sdk.HookMatcher(hooks=[guard_tool])]},
                    system_prompt=(
                        "Read the bound current task with read_context. Preserve its goals, "
                        "priorities, rules and obligations. Explore alternative ideas, but "
                        "submit results only through submit_candidate as unapproved DRAFTs. "
                        "Declare the source-field revisions actually consumed and their "
                        "completeness. A hypothesis is not an established fact. You cannot "
                        "change the task, approve checks, release obligations or authorize "
                        "use. No prose response is an approved final result."
                    ),
                )

                async def execute() -> None:
                    async with sdk.ClaudeSDKClient(options=options) as client:
                        await client.query(prompt)
                        await receive(client)

                await asyncio.wait_for(execute(), timeout=self._timeout)
            except DreamkeeperSDKError:
                raise
            except Exception as exc:
                # No fallback model, permissive options, direct API, or raw-output path.
                raise DreamkeeperSDKError("SDK run stopped; no final result was issued") from exc

        return DraftRun(tuple(candidates), binding.logical_id, binding.task_id, binding.run_id)

    def consume_final(self, binding: RunBinding, *, candidate_id: str,
                      approval_id: str, purpose: str) -> dict[str, Any]:
        """Host-only finalization; the engine rechecks approval and current state."""
        return self._engine.consume(binding.logical_id, binding.task_id,
                                    candidate_id=candidate_id, approval_id=approval_id,
                                    purpose=purpose)
