"""Run validation first, then a time-bounded worker; optionally stop its Runpod Pod.

No Pod is created or billed by this script. A provider-side stop must succeed
before GPU billing stops. This is a watchdog, not a guaranteed account spending cap.
"""
from __future__ import annotations
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--seconds", type=int, default=14400)
    p.add_argument("--stop-runpod", action="store_true")
    p.add_argument("worker_args", nargs=argparse.REMAINDER)
    a = p.parse_args()
    if not 60 <= a.seconds <= 14400:
        p.error("Pilot duration must be between 60 seconds and 4 hours")
    forwarded = a.worker_args[1:] if a.worker_args[:1] == ["--"] else a.worker_args
    if not forwarded or "--check-only" in forwarded:
        p.error("Pass training worker arguments after --")
    runner = Path(__file__).with_name("train_gpt_oss_grpo.py")
    command = [sys.executable, str(runner), *forwarded]
    pod = os.environ.get("RUNPOD_POD_ID")
    if a.stop_runpod:
        import shutil
        if not pod or not shutil.which("runpodctl"):
            p.error("--stop-runpod needs RUNPOD_POD_ID and authenticated runpodctl")
    elif pod:
        p.error("On Runpod, use --stop-runpod and configure runpodctl before launch")
    def interrupted(signum, frame):
        raise KeyboardInterrupt("Launcher interrupted")
    signal.signal(signal.SIGTERM, interrupted)
    process = None
    code = 1
    stop_ok = None
    try:
        # Preflight is inside finally so a failed validation also requests Pod stop.
        subprocess.run(command + ["--check-only"], check=True, timeout=120)
        process = subprocess.Popen(command, start_new_session=True)
        try:
            code = process.wait(timeout=a.seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if a.stop_runpod:
            stop_ok = False
            for attempt in range(3):
                try:
                    result = subprocess.run(["runpodctl", "pod", "stop", pod],
                                            capture_output=True, text=True, timeout=30)
                    if result.returncode == 0:
                        stop_ok = True
                        break
                except (OSError, subprocess.TimeoutExpired):
                    pass
                time.sleep(2)
        print(json.dumps({"worker_exit_code": code, "pod_stop_command_succeeded": stop_ok,
                          "billing_stop_must_be_verified_in_provider_console": True}))
        if stop_ok is False:
            print("ACTION REQUIRED: Pod stop was not confirmed. Stop the Pod in Runpod console now.", file=sys.stderr)
    raise SystemExit(code if stop_ok is not False else 125)

if __name__ == "__main__":
    main()
