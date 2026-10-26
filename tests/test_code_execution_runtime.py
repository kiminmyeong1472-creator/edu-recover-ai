"""Offline lifecycle tests; the fake VM executes helper code in a temp folder.

These tests make no Railway calls and never run the package bootstrap. The
production module itself has no local subprocess execution path.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.services import code_execution_runtime as runtime


class FakeRailway:
    def __init__(self, folder: str) -> None:
        self.folder = folder
        self.calls: list[tuple[str, dict]] = []
        self.status = "RUNNING"
        self.fail_bootstrap = False
        self.fail_destroy = False
        self.fail_exec = False
        self.bad_isolation = False

    def __call__(self, query: str, variables: dict) -> dict:
        self.calls.append((query, variables))
        if "sandboxCreate" in query:
            return {"sandboxCreate": {"id": "test-vm", "status": self.status,
                    "networkIsolation": "PRIVATE" if self.bad_isolation else "ISOLATED"}}
        if "sandboxDestroy" in query:
            if self.fail_destroy:
                raise runtime.CodeExecutionError("SERVICE_UNAVAILABLE")
            return {"sandboxDestroy": {"id": "test-vm", "status": "DESTROYED"}}
        if "sandboxExec" in query:
            if self.fail_exec:
                raise runtime.CodeExecutionError("SERVICE_UNAVAILABLE")
            detached = self._detached(variables["command"])
            if detached is not None:
                return detached
            if variables["command"] == runtime._BOOTSTRAP:
                return {"sandboxExec": {"exitCode": 72 if self.fail_bootstrap else 0,
                        "stdout": "ready\n", "stderr": "", "timedOut": False, "truncated": False}}
            command = shlex.split(variables["command"])
            payload = json.loads(base64.b64decode(command[-1]))
            if payload["action"] == "preflight":
                return {"sandboxExec": {"exitCode": 0, "stdout": json.dumps({"tools": {
                    name: True for name in ("python3", "bash", "git", "node", "npm", "gcc", "pytest")}}),
                    "stderr": "", "truncated": False, "timedOut": False}}
            script_index = command.index("-c") + 1
            script = command[script_index].replace('ROOT = "/workspace/project"',
                                                   "ROOT = " + repr(self.folder))
            # Only the test fake runs the helper locally. All test programs run
            # with a temp cwd and no platform credentials or bootstrap. Test
            # programs never request network access.
            result = subprocess.run([sys.executable, "-I", "-c", script, command[-1]],
                                    capture_output=True, text=True,
                                    timeout=variables["timeoutSec"] + 2,
                                    env={"PATH": "/usr/local/bin:/usr/bin:/bin"})
            return {"sandboxExec": {"exitCode": result.returncode,
                    "stdout": result.stdout, "stderr": result.stderr,
                    "truncated": False, "timedOut": False}}
        if "sandbox(" in query:
            return {"sandbox": {"id": "test-vm", "status": self.status,
                    "networkIsolation": "ISOLATED"}}
        raise AssertionError("Unknown query")


    def _detached(self, text: str):
        """Mirror the background-job protocol used for long commands."""
        import re
        jobs = self.__dict__.setdefault("jobs", {})
        started = re.match(r"mkdir -p (\S+) && printf %s (\S+) \| base64 -d", text)
        if started:
            inner = base64.b64decode(started.group(2)).decode()
            answer = self("sandboxExec", {**self.calls[-1][1], "command": inner})["sandboxExec"]
            self.calls.pop()
            jobs[started.group(1)] = answer
            return {"sandboxExec": {"exitCode": 0, "stdout": "started\n", "stderr": "",
                                    "truncated": False, "timedOut": False}}
        for job, answer in jobs.items():
            if text.startswith("cat %s/rc" % job):
                return {"sandboxExec": {"exitCode": 0, "stdout": "%d\n" % answer["exitCode"],
                                        "stderr": "", "truncated": False, "timedOut": False}}
            if text.startswith("head -c 15000 %s/out" % job):
                return {"sandboxExec": {"exitCode": 0, "stdout": answer["stdout"], "stderr": "",
                                        "truncated": False, "timedOut": False}}
        return None


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.fake = FakeRailway(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def session(self, **kwargs) -> runtime.RailwayCodeSession:
        kwargs.setdefault("sleep", lambda _: None)
        return runtime.RailwayCodeSession(graphql=self.fake, environment_id="test-environment", **kwargs)

    def assert_code(self, code: str):
        return self.assertRaisesRegex(runtime.CodeExecutionError, "^CODE_EXECUTION_" + code + "$")

    def test_provider_independent_write_execute_read_export_destroy(self) -> None:
        with self.session() as session:
            session.seed([{"path": "main.py", "content": "print(1 + 2)\n"}])
            result = session.execute("run_command", {"argv": ["python3", "main.py"],
                                                     "cwd": ".", "timeout_seconds": 5})
            self.assertEqual((result["exit_code"], result["stdout"]), (0, "3\n"))
            self.assertEqual(session.execute("file_read", {"path": "main.py"})["content"], "print(1 + 2)\n")
        self.assertTrue(session.cleanup_status["confirmed"])
        self.assertEqual(session.snapshot()[0]["content"], "print(1 + 2)\n")
        self.assertEqual(session.snapshot()[0]["encoding"], "utf-8")
        created = self.fake.calls[0][1]["input"]
        self.assertEqual(created, {"environmentId": "test-environment", "idleTimeoutMinutes": 5,
                                  "networkIsolation": "ISOLATED", "resources": {"cpu": 1, "memoryGB": 1},
                                  "variables": {}})
        self.assertIn("sandboxDestroy", self.fake.calls[-1][0])

    def test_clean_environment_and_no_shell_interpolation(self) -> None:
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "must-not-leak", "RAILWAY_TOKEN": "platform-secret"}):
            with self.session() as session:
                result = session.execute("run_command", {"argv": ["python3", "-c",
                    "import os,sys;print(os.environ.get('DEEPSEEK_API_KEY'));print(os.environ.get('RAILWAY_TOKEN'));print(sys.argv[1])",
                    "$(touch injected); `id`"], "timeout_seconds": 5})
                self.assertEqual(result["stdout"], "None\nNone\n$(touch injected); `id`\n")
                self.assertFalse(Path(self.temp.name, "injected").exists())
                self.assertFalse(any("must-not-leak" in str(v) or "platform-secret" in str(v)
                                     for _, v in self.fake.calls))

    def test_paths_rejected_before_remote_call(self) -> None:
        with self.session() as session:
            for path in ("../etc/passwd", "/etc/passwd", "a/../b", "a//b", "./a", "a\\b", "a\x00b", "a\nb"):
                before = len(self.fake.calls)
                with self.subTest(path=repr(path)), self.assert_code("PATH_INVALID"):
                    session.execute("file_read", {"path": path})
                self.assertEqual(len(self.fake.calls), before)

    def test_symlink_read_write_and_cwd_rejected(self) -> None:
        outside = Path(self.temp.name).parent / (Path(self.temp.name).name + "-outside")
        outside.mkdir()
        try:
            (outside / "secret.txt").write_text("outside")
            Path(self.temp.name, "link").symlink_to(outside, target_is_directory=True)
            with self.session() as session:
                for name, args in (("file_read", {"path": "link/secret.txt"}),
                                   ("file_write", {"path": "link/new.txt", "content": "bad"}),
                                   ("run_command", {"cwd": "link", "argv": ["pwd"]})):
                    with self.subTest(name=name), self.assert_code("REMOTE_FAILED"):
                        session.execute(name, args)
                self.assertEqual(session.execute("file_list", {})["files"], [])
                self.assertFalse((outside / "new.txt").exists())
        finally:
            (outside / "secret.txt").unlink()
            outside.rmdir()

    def test_utf8_multichunk_roundtrip_and_quoted_file_name(self) -> None:
        content = "한글😀'\n" * 1100
        with self.session() as session:
            session.execute("file_write", {"path": "nested/a ' b.py", "content": content})
            result = session.execute("file_read", {"path": "nested/a ' b.py"})
            self.assertEqual(result["content"], content)
            self.assertEqual(session.snapshot(), [result])

    def test_full_file_limit_uses_paged_writes_without_oversized_argv(self) -> None:
        content = "x" * runtime.MAX_FILE_BYTES
        with self.session() as session:
            session.execute("file_write", {"path": "limit.txt", "content": content})
            self.assertEqual(session.execute("file_read", {"path": "limit.txt"})["content"], content)
            for query, variables in self.fake.calls:
                if "sandboxExec" in query:
                    self.assertLess(len(variables["command"].encode()), 32768)

    def test_failed_exit_is_returned_for_model_to_repair(self) -> None:
        with self.session() as session:
            result = session.execute("run_command", {"argv": ["python3", "-c", "raise ValueError('broken')"],
                                                     "timeout_seconds": 5})
            self.assertNotEqual(result["exit_code"], 0)
            self.assertIn("ValueError: broken", result["stderr"])

    def test_output_is_drained_and_bounded(self) -> None:
        with self.session() as session:
            result = session.execute("run_command", {"argv": ["python3", "-c",
                "import sys;sys.stdout.write('a'*100000);sys.stderr.write('b'*100000)"], "timeout_seconds": 5})
            self.assertEqual(result["exit_code"], 0)
            self.assertEqual(len(result["stdout"]), 3072)
            self.assertEqual(len(result["stderr"]), 3072)
            self.assertTrue(result["truncated"])

    def test_command_timeout_terminates_group_and_returns_result(self) -> None:
        with self.session() as session:
            result = session.execute("run_command", {"argv": ["python3", "-c", "import time; time.sleep(30)"],
                                                     "timeout_seconds": 1})
            self.assertTrue(result["timed_out"])
            self.assertNotEqual(result["exit_code"], 0)
        self.assertTrue(session.cleanup_status["confirmed"])

    def test_no_local_fallback_when_remote_fails(self) -> None:
        with self.session() as session:
            self.fake.fail_exec = True
            with self.assert_code("SERVICE_UNAVAILABLE"):
                session.execute("run_command", {"argv": ["touch", "must-not-exist"]})
            self.fake.fail_exec = False
            self.assertFalse(Path(self.temp.name, "must-not-exist").exists())

    def test_body_exception_still_exports_and_destroys(self) -> None:
        session = self.session()
        with self.assertRaisesRegex(ValueError, "body failed"):
            with session:
                session.execute("file_write", {"path": "saved.py", "content": "pass"})
                raise ValueError("body failed")
        self.assertEqual(session.snapshot()[0]["content"], "pass")
        self.assertTrue(session.cleanup_status["confirmed"])

    def test_failed_bootstrap_destroys_vm(self) -> None:
        self.fake.fail_bootstrap = True
        session = self.session()
        with self.assert_code("TOOLS_MISSING"):
            with session:
                self.fail("must not enter")
        self.assertTrue(session.cleanup_status["confirmed"])

    def test_wrong_isolation_destroys_before_any_command(self) -> None:
        self.fake.bad_isolation = True
        session = self.session()
        with self.assert_code("ISOLATION_FAILED"):
            with session:
                self.fail("must not enter")
        self.assertTrue(session.cleanup_status["confirmed"])
        self.assertFalse(any("sandboxExec" in query for query, _ in self.fake.calls))

    def test_cleanup_failure_is_reported_as_unconfirmed(self) -> None:
        self.fake.fail_destroy = True
        session = self.session()
        with self.assert_code("CLEANUP_UNCONFIRMED"):
            with session:
                pass
        self.assertEqual(session.cleanup_status, {"confirmed": False, "status": "cleanup_unconfirmed"})

    def test_binary_export_and_reseed(self) -> None:
        session = self.session()
        with session:
            Path(self.temp.name, "binary.bin").write_bytes(b"\xff\x00\x80")
        saved = session.snapshot()[0]
        self.assertEqual(saved["encoding"], "base64")
        self.assertEqual(base64.b64decode(saved["content"]), b"\xff\x00\x80")
        self.assertEqual(saved["bytes"], 3)
        Path(self.temp.name, "binary.bin").unlink()
        with self.session() as other:
            other.seed([saved])
            self.assertEqual(Path(self.temp.name, "binary.bin").read_bytes(), b"\xff\x00\x80")
        self.assertTrue(session.cleanup_status["confirmed"])

    def test_oversized_generated_file_is_metadata_not_job_failure(self) -> None:
        with self.session() as session:
            Path(self.temp.name, "huge.bin").write_bytes(b"x" * (runtime.MAX_FILE_BYTES + 1))
            session.execute("file_write", {"path": "kept.py", "content": "print('ok')"})
        saved = {item["path"]: item for item in session.snapshot()}
        self.assertFalse(saved["huge.bin"]["exported"])
        self.assertEqual(saved["huge.bin"]["reason"], "file_size_limit")
        self.assertNotIn("content", saved["huge.bin"])
        self.assertTrue(saved["kept.py"]["exported"])

    def test_cpp_compile_and_run_exports_executable(self) -> None:
        with self.session() as session:
            session.execute("file_write", {"path": "main.c", "content": '#include <stdio.h>\nint main(void){puts("ok");return 0;}\n'})
            compiled = session.execute("run_command", {"argv": ["gcc", "main.c", "-o", "program"], "timeout_seconds": 10})
            self.assertEqual(compiled["exit_code"], 0, compiled["stderr"])
            result = session.execute("run_command", {"argv": ["./program"], "timeout_seconds": 5})
            self.assertEqual(result["stdout"], "ok\n")
        saved = {item["path"]: item for item in session.snapshot()}
        self.assertEqual(saved["program"]["encoding"], "base64")
        self.assertTrue(saved["program"]["exported"])

    def test_request_limits_and_unknown_tools(self) -> None:
        with self.session() as session:
            for args in ({"argv": "python3 -c hi"}, {"argv": []}, {"argv": ["x\x00"]},
                         {"argv": ["true"], "timeout_seconds": 301},
                         {"argv": ["true"], "timeout_seconds": True}):
                with self.subTest(args=args), self.assert_code("COMMAND_INVALID"):
                    session.execute("run_command", args)
            with self.assert_code("FILES_TOO_LARGE"):
                session.execute("file_write", {"path": "big", "content": "x" * (runtime.MAX_FILE_BYTES + 1)})
            with self.assert_code("TOOL_INVALID"):
                session.execute("shell", {})
            with self.assert_code("FILES_TOO_MANY"):
                session.seed([{"path": str(i), "content": ""} for i in range(65)])

    def test_paged_listing(self) -> None:
        for number in range(34):
            Path(self.temp.name, f"file{number:02}.txt").write_text(str(number))
        with self.session() as session:
            files = session.execute("file_list", {})["files"]
            self.assertEqual(len(files), 34)
            self.assertEqual(files[0]["path"], "file00.txt")
            self.assertEqual(len(session.snapshot()), 34)

    def test_seed_validates_all_paths_before_writing(self) -> None:
        with self.session() as session:
            with self.assert_code("PATH_INVALID"):
                session.seed([{"path": "good", "content": "yes"}, {"path": "../bad", "content": "no"}])
            self.assertFalse(Path(self.temp.name, "good").exists())

    def test_runtime_status_reports_config_without_claiming_verified(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(runtime.runtime_status()["configured"])
            with self.assert_code("NOT_CONFIGURED"):
                runtime.RailwayCodeSession()
        with patch.dict(os.environ, {"RAILWAY_TOKEN": "hidden", "RAILWAY_ENVIRONMENT_ID": "env-test"}, clear=True):
            status = runtime.runtime_status()
            self.assertTrue(status["configured"])
            self.assertFalse(status["verified"])
            self.assertNotIn("hidden", json.dumps(status))

    def test_closed_session_cannot_execute_new_commands(self) -> None:
        with self.session() as session:
            pass
        with self.assert_code("SESSION_INVALID"):
            session.execute("run_command", {"argv": ["true"]})


if __name__ == "__main__":
    unittest.main()
