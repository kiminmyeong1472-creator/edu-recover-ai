import shutil
import subprocess
import time
import unittest

from app.services import code_execution_runtime as rt


def local_shell(query, variables):
    if query != rt._EXEC:
        raise AssertionError(query)
    done = subprocess.run(["/bin/sh", "-c", variables["command"]], capture_output=True,
                          text=True, timeout=variables["timeoutSec"])
    return {"sandboxExec": {"exitCode": done.returncode, "stdout": done.stdout,
                            "stderr": done.stderr, "truncated": False, "timedOut": False}}


@unittest.skipUnless(shutil.which("setsid") and shutil.which("timeout"), "needs coreutils")
class DetachedExecTests(unittest.TestCase):
    def session(self):
        s = rt.RailwayCodeSession(graphql=local_shell, environment_id="env",
                                  sleep=lambda _: time.sleep(0.2))
        s.sandbox_id, s._ready, s._deadline = "sb", True, time.monotonic() + 600
        s._jobs = int(time.time() * 1000) % 10**9
        return s

    def test_long_command_runs_in_background_and_returns_output(self):
        result = self.session()._exec("sleep 1; printf 'ready\\n'", 180)
        self.assertEqual(result["stdout"], "ready\n")
        self.assertEqual(result["exitCode"], 0)

    def test_failure_exit_code_is_reported(self):
        with self.assertRaises(rt.CodeExecutionError) as caught:
            self.session()._exec("exit 3", 120)
        self.assertEqual(caught.exception.code, "CODE_EXECUTION_REMOTE_FAILED")

    def test_short_command_stays_direct(self):
        calls = []

        def spy(q, v):
            calls.append(v["command"])
            return local_shell(q, v)
        s = self.session()
        s._graphql = spy
        s._exec("echo hi", 15)
        self.assertEqual(calls, ["echo hi"])


if __name__ == "__main__":
    unittest.main()
