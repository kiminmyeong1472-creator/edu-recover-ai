"""Provider-independent code tools backed only by disposable Railway VMs.

No user program runs in the EduRecover web process. API credentials stay in
that process: a new ISOLATED VM receives an empty variable map. Files are
exported before the VM is destroyed, and unconfirmed destruction is an error.

GraphQL operations match railwayapp/railway-ts-sdk at 97fdb4f (2026-10-17).
HTTPS sandboxExec is deliberately used instead of a local Railway subprocess.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import shlex
import socket
import ssl
import time
from typing import Any, Callable


GRAPHQL_HOST = "backboard.railway.com"
GRAPHQL_PATH = "/graphql/v2"
MAX_FILES = 64
MAX_FILE_BYTES = 128 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_COMMAND_SECONDS = 300
MAX_SESSION_SECONDS = 600
MAX_RESPONSE_BYTES = 128 * 1024
_DIRECT_EXEC_SECONDS = 45
_CHUNK_BYTES = 4096
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")

_CREATE = """mutation EduRecoverCodeCreate($input: SandboxCreateInput!) {
 sandboxCreate(input: $input) { id status networkIsolation }
}"""
_GET = """query EduRecoverCodeStatus($environmentId: String!, $id: String!) {
 sandbox(environmentId: $environmentId, id: $id) { id status networkIsolation }
}"""
_EXEC = """mutation EduRecoverCodeExec($environmentId: String!, $id: String!,
 $command: String!, $timeoutSec: Int) {
 sandboxExec(environmentId: $environmentId, id: $id, command: $command,
 timeoutSec: $timeoutSec) { exitCode stdout stderr truncated timedOut }
}"""
_DESTROY = """mutation EduRecoverCodeDestroy($environmentId: String!, $id: String!) {
 sandboxDestroy(environmentId: $environmentId, id: $id) { id status }
}"""
_BOOTSTRAP = "env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin HOME=/root LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive /bin/sh -c " + shlex.quote(r'''
# Best effort: install what is missing with whatever package manager exists.
# Only python3 is required (the file helper runs on it); the rest is optional.
missing=""
for pair in "python3:python3" "bash:bash" "git:git" "node:nodejs" "npm:npm" "gcc:gcc"; do
  tool=${pair%%:*}; pkg=${pair#*:}
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $pkg"
done
if command -v python3 >/dev/null 2>&1 && ! python3 -m pytest --version >/dev/null 2>&1; then
  missing="$missing python3-pytest"
fi
if [ -n "$missing" ]; then
  run=""
  if [ "$(id -u)" = 0 ]; then run=""; elif command -v sudo >/dev/null 2>&1; then run="sudo -n"; else run="none"; fi
  if [ "$run" != "none" ]; then
    if command -v apt-get >/dev/null 2>&1; then
      $run apt-get update -qq >/dev/null 2>&1 || true
      for pkg in $missing; do $run apt-get install -y -qq --no-install-recommends "$pkg" >/dev/null 2>&1 || true; done
    elif command -v apk >/dev/null 2>&1; then
      for pkg in $missing; do
        case "$pkg" in python3-pytest) pkg=py3-pytest;; esac
        $run apk add --no-cache "$pkg" >/dev/null 2>&1 || true
      done
    elif command -v dnf >/dev/null 2>&1; then
      for pkg in $missing; do $run dnf install -y -q "$pkg" >/dev/null 2>&1 || true; done
    fi
  fi
fi
if command -v python3 >/dev/null 2>&1 && ! python3 -m pytest --version >/dev/null 2>&1; then
  python3 -m pip install -q pytest >/dev/null 2>&1 || python3 -m pip install -q --break-system-packages pytest >/dev/null 2>&1 || true
fi
command -v python3 >/dev/null 2>&1 || { echo "no-python3"; exit 72; }
printf 'ready\n'
''')


class CodeExecutionError(Exception):
    """A stable public code, never upstream bodies, tokens, or request data."""

    def __init__(self, code: str) -> None:
        self.code = code if code.startswith("CODE_EXECUTION_") else "CODE_EXECUTION_" + code
        super().__init__(self.code)


def _credentials() -> tuple[str, str]:
    project_token = os.environ.get("RAILWAY_TOKEN", "")
    return (project_token, "project-access-token") if project_token else (
        os.environ.get("RAILWAY_API_TOKEN", ""), "Authorization")


def runtime_status() -> dict[str, Any]:
    token, _ = _credentials()
    environment = os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
    configured = bool(token and _ID.fullmatch(environment))
    return {
        "backend": "railway_sandbox",
        "configured": configured,
        "verified": False,
        "message": ("격리 실행 인증이 설정되어 있습니다. 실제 실행 가능 여부는 실행할 때 확인합니다."
                    if configured else "격리 실행에 필요한 Railway 인증 키와 환경 식별자가 필요합니다."),
        "tools": ["file_list", "file_read", "file_write", "run_command"],
        "limits": {"files": MAX_FILES, "file_bytes": MAX_FILE_BYTES,
                   "total_bytes": MAX_TOTAL_BYTES, "command_seconds": MAX_COMMAND_SECONDS},
    }


# Railway answers many refusals with HTTP 200 plus GraphQL errors. Only a
# fixed category is kept; the upstream text itself is never stored or shown.
_REJECTION_RULES = (
    ("ACCESS_DENIED", ("not authorized", "unauthorized", "forbidden",
                       "permission", "invalid token", "token is invalid")),
    ("PLAN_LIMIT", ("limit", "exceed", "maximum", "quota", "upgrade", "plan",
                    "trial", "credit", "billing", "payment")),
    ("ENVIRONMENT_INVALID", ("environment", "not found", "does not exist")),
    ("SCHEMA_MISMATCH", ("cannot query field", "unknown argument", "unknown type",
                         "is not defined", "validation", "expected type")),
)


def _classify_rejection(text: str) -> str:
    for code, needles in _REJECTION_RULES:
        if any(needle in text for needle in needles):
            return code
    return "REQUEST_REJECTED"


_PROJECT_TOKEN_ENV = """query EduRecoverTokenEnvironment { projectToken { environmentId } }"""
# Index of the token/header pair Railway last accepted, and the environment a
# project token is actually scoped to when it differs from the service's own.
_accepted: dict[str, Any] = {"candidate": None, "environment": None}


def _candidates() -> list[tuple[str, str]]:
    """Every way the configured tokens could be valid, preferred order first.

    A token pasted into the wrong variable (account token in RAILWAY_TOKEN, or a
    stale RAILWAY_TOKEN shadowing a fresh RAILWAY_API_TOKEN) is otherwise
    refused as "Not Authorized" even though the token itself is fine.
    """
    project = os.environ.get("RAILWAY_TOKEN", "")
    account = os.environ.get("RAILWAY_API_TOKEN", "")
    found: list[tuple[str, str]] = []
    for pair in ((project, "project-access-token"), (account, "Authorization"),
                 (project, "Authorization"), (account, "project-access-token")):
        if pair[0] and pair not in found:
            found.append(pair)
    return found


def _with_environment(variables: dict[str, Any], environment: str) -> dict[str, Any]:
    out = dict(variables)
    if "environmentId" in out:
        out["environmentId"] = environment
    if isinstance(out.get("input"), dict) and "environmentId" in out["input"]:
        out["input"] = {**out["input"], "environmentId": environment}
    return out


def _graphql_request(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    candidates = _candidates()
    if not candidates:
        raise CodeExecutionError("NOT_CONFIGURED")
    if _accepted["environment"]:
        variables = _with_environment(variables, _accepted["environment"])
    start = _accepted["candidate"] if _accepted["candidate"] is not None else 0
    order = [i for i in range(len(candidates)) if i == start] + [
        i for i in range(len(candidates)) if i != start]
    denied: CodeExecutionError | None = None
    for index in order:
        token, header_name = candidates[index]
        try:
            data = _graphql_send(query, variables, token, header_name)
            _accepted["candidate"] = index
            return data
        except CodeExecutionError as error:
            if error.code != "CODE_EXECUTION_ACCESS_DENIED":
                raise
            denied = error
        if header_name == "project-access-token" and not _accepted["environment"]:
            # A project token only works in its own environment; ask Railway
            # which one that is and retry there (the request ran nowhere yet).
            try:
                scoped = _graphql_send(_PROJECT_TOKEN_ENV, {}, token, header_name)
                environment = (scoped.get("projectToken") or {}).get("environmentId")
            except CodeExecutionError:
                environment = None
            if isinstance(environment, str) and _ID.fullmatch(environment):
                moved = _with_environment(variables, environment)
                if moved != variables:
                    try:
                        data = _graphql_send(query, moved, token, header_name)
                        _accepted.update(candidate=index, environment=environment)
                        return data
                    except CodeExecutionError as error:
                        if error.code != "CODE_EXECUTION_ACCESS_DENIED":
                            raise
    raise denied or CodeExecutionError("ACCESS_DENIED")


def _graphql_send(query: str, variables: dict[str, Any], token: str,
                  header_name: str) -> dict[str, Any]:
    if (not token or len(token) > 8192
            or any(ord(ch) <= 32 or ord(ch) >= 127 for ch in token)):
        raise CodeExecutionError("NOT_CONFIGURED")
    try:
        body = json.dumps({"query": query, "variables": variables},
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise CodeExecutionError("INPUT_INVALID") from None
    if len(body) > 2 * 1024 * 1024:
        raise CodeExecutionError("INPUT_TOO_LARGE")
    # The host and path cannot be supplied by a browser or model. TLS verifies
    # the fixed Railway host, proxies are not used, and redirects are rejected.
    timeout = min(MAX_COMMAND_SECONDS + 30, max(30, variables.get("timeoutSec", 0) + 15))
    connection = http.client.HTTPSConnection(GRAPHQL_HOST, timeout=timeout,
                                             context=ssl.create_default_context())
    response = None
    try:
        connection.request("POST", GRAPHQL_PATH, body=body, headers={
            header_name: ("Bearer " + token if header_name == "Authorization" else token),
            "Content-Type": "application/json", "Accept": "application/json",
            "Accept-Encoding": "identity", "User-Agent": "EduRecover-CodeTools/1",
        })
        response = connection.getresponse()
        if response.status in (401, 403):
            raise CodeExecutionError("ACCESS_DENIED")
        if not 200 <= response.status < 300:
            raise CodeExecutionError("SERVICE_UNAVAILABLE")
        if response.getheader("Content-Encoding", "identity") != "identity":
            raise CodeExecutionError("RESPONSE_INVALID")
        chunks, size, deadline = [], 0, time.monotonic() + timeout
        while True:
            if time.monotonic() > deadline:
                raise CodeExecutionError("TIMEOUT")
            chunk = response.read1(min(8192, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise CodeExecutionError("RESPONSE_TOO_LARGE")
        result = json.loads(b"".join(chunks).decode("utf-8"))
        if not isinstance(result, dict):
            raise CodeExecutionError("RESPONSE_INVALID")
        if result.get("errors"):
            # Only classify a known feature gate; upstream text is never saved.
            text = str(result["errors"]).lower()
            if "project_sandboxes" in text or ("sandbox" in text and "not enabled" in text):
                raise CodeExecutionError("FEATURE_UNAVAILABLE")
            raise CodeExecutionError(_classify_rejection(text))
        if not isinstance(result.get("data"), dict):
            raise CodeExecutionError("RESPONSE_INVALID")
        return result["data"]
    except CodeExecutionError:
        raise
    except (TimeoutError, socket.timeout):
        raise CodeExecutionError("TIMEOUT") from None
    except (OSError, ValueError, TypeError, UnicodeError, http.client.HTTPException):
        raise CodeExecutionError("SERVICE_UNAVAILABLE") from None
    finally:
        if response is not None:
            response.close()
        connection.close()


def _path(value: Any, *, directory: bool = False) -> str:
    if directory and value == ".":
        return "."
    try:
        encoded_length = len(value.encode("utf-8")) if isinstance(value, str) else 0
    except UnicodeError:
        raise CodeExecutionError("PATH_INVALID") from None
    if (not isinstance(value, str) or not value or encoded_length > 240
            or value.startswith("/") or "\\" in value
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
            or any(part in ("", ".", "..") for part in value.split("/"))):
        raise CodeExecutionError("PATH_INVALID")
    return value


# This helper is sent as a quoted python -c argument to the *remote* VM. The
# descriptor-relative file operations reject symlink traversal, including races
# where a previously checked directory is replaced by user code.
_REMOTE_HELPER = r'''
import base64, json, os, selectors, signal, stat, subprocess, sys, time
ROOT = "/workspace/project"
MAX_FILE = 131072
MAX_FILES = 64
MAX_TOTAL = 524288
SKIP = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache"}
request = json.loads(base64.b64decode(sys.argv[1]))
def checked(value, directory=False):
    if directory and value == ".": return []
    if not isinstance(value, str) or not value or len(value.encode()) > 240 or value.startswith("/") or "\\" in value or any(ord(c)<32 or ord(c)==127 for c in value): raise ValueError("PATH_INVALID")
    parts=value.split("/")
    if any(p in ("", ".", "..") for p in parts): raise ValueError("PATH_INVALID")
    return parts
def rootfd():
    fd=os.open("/",os.O_RDONLY|os.O_DIRECTORY)
    try:
        for name in ROOT.strip("/").split("/"):
            other=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd); fd=other
        return fd
    except BaseException:
        os.close(fd); raise
def parent(parts, create=False):
    fd=rootfd()
    try:
        for name in parts[:-1]:
            if create:
                try: os.mkdir(name, mode=0o700, dir_fd=fd)
                except FileExistsError: pass
            other=os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd=other
        return fd
    except BaseException:
        os.close(fd); raise
def walk_files(parts):
    found=[]; visited=0; truncated=False
    def walk(fd, prefix):
        nonlocal visited, truncated
        for entry in os.scandir(fd):
            name=entry.name
            visited+=1
            if visited>4096 or len(found)>=MAX_FILES:
                truncated=True; return
            if name in SKIP: continue
            child=prefix+[name]; rel="/".join(child)
            checked(rel)
            entry=os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(entry.st_mode): continue
            if stat.S_ISDIR(entry.st_mode):
                sub=os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                try: walk(sub, child)
                finally: os.close(sub)
                if truncated: return
            elif stat.S_ISREG(entry.st_mode):
                found.append({"path":rel,"bytes":entry.st_size})
    fd=rootfd()
    try:
        for name in parts:
            other=os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd=other
        walk(fd, parts)
    finally: os.close(fd)
    return sorted(found,key=lambda item:item["path"]),truncated
def main():
    action=request["action"]
    if action=="preflight":
        os.makedirs(ROOT, mode=0o700, exist_ok=True)
        fd=rootfd(); os.close(fd)
        import shutil, importlib.util
        tools={name:bool(shutil.which(name)) for name in ("python3","bash","git","node","npm","gcc")}
        tools["pytest"]=importlib.util.find_spec("pytest") is not None
        return {"tools":tools}
    if action=="list":
        files,truncated=walk_files(checked(request["path"], True))
        offset=request.get("offset",0)
        return {"files":files[offset:offset+16],"count":len(files),"truncated":truncated}
    parts=checked(request.get("path", "."), action=="run")
    if action=="read":
        fd=parent(parts)
        try: handle=os.open(parts[-1],os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=fd)
        finally: os.close(fd)
        try:
            info=os.fstat(handle)
            if not stat.S_ISREG(info.st_mode): raise ValueError("PATH_INVALID")
            if info.st_size>MAX_FILE: return {"data":"","bytes":info.st_size,"exported":False,"reason":"file_size_limit"}
            os.lseek(handle, request.get("offset",0), os.SEEK_SET)
            data=os.read(handle,4096)
            return {"data":base64.b64encode(data).decode(),"bytes":info.st_size}
        finally: os.close(handle)
    if action=="write":
        data=base64.b64decode(request["data"], validate=True)
        offset=request.get("offset",0)
        if type(offset) is not int or offset<0 or offset+len(data)>MAX_FILE: raise ValueError("FILES_TOO_LARGE")
        fd=parent(parts, True)
        flags=os.O_WRONLY|os.O_NOFOLLOW|os.O_NONBLOCK
        flags|=os.O_CREAT|os.O_TRUNC if offset==0 else 0
        try: handle=os.open(parts[-1],flags,0o600,dir_fd=fd)
        finally: os.close(fd)
        try:
            info=os.fstat(handle)
            if not stat.S_ISREG(info.st_mode): raise ValueError("PATH_INVALID")
            if info.st_size!=offset: raise ValueError("PATH_INVALID")
            os.lseek(handle,offset,os.SEEK_SET)
            with os.fdopen(handle,"wb",closefd=False) as target: target.write(data)
        finally: os.close(handle)
        return {"path":request["path"],"bytes":offset+len(data)}
    if action=="run":
        fd=rootfd()
        try:
            for name in parts:
                other=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd); fd=other
            cwd="/proc/self/fd/"+str(fd)
            env={"PATH":"/usr/local/bin:/usr/bin:/bin","HOME":"/tmp/edurecover-home","LANG":"C.UTF-8","PYTHONUNBUFFERED":"1"}
            os.makedirs(env["HOME"],mode=0o700,exist_ok=True)
            proc=subprocess.Popen(request["argv"],cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,pass_fds=(fd,))
        finally: os.close(fd)
        capture={"stdout":bytearray(),"stderr":bytearray()}; truncated=False; timed_out=False
        selector=selectors.DefaultSelector()
        selector.register(proc.stdout,selectors.EVENT_READ,"stdout"); selector.register(proc.stderr,selectors.EVENT_READ,"stderr")
        deadline=time.monotonic()+request["timeout_seconds"]
        while selector.get_map():
            if time.monotonic()>deadline:
                timed_out=True
                try: os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError: pass
                break
            for key,_ in selector.select(min(0.1,max(0,deadline-time.monotonic()))):
                chunk=os.read(key.fileobj.fileno(),8192)
                if not chunk: selector.unregister(key.fileobj); continue
                available=max(0,3072-len(capture[key.data]))
                capture[key.data].extend(chunk[:available])
                if len(chunk)>available: truncated=True
        # A successful parent may leave a background child holding the pipes.
        # Kill the complete group after collecting the foreground result too.
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        try: exit_code=proc.wait(timeout=2)
        except subprocess.TimeoutExpired: exit_code=None
        selector.close(); proc.stdout.close(); proc.stderr.close()
        return {"exit_code":exit_code,"stdout_b64":base64.b64encode(capture["stdout"]).decode(),"stderr_b64":base64.b64encode(capture["stderr"]).decode(),"truncated":truncated,"timed_out":timed_out}
    raise ValueError("TOOL_INVALID")
try:
    result=main()
except (OSError,ValueError,TypeError,KeyError) as exc:
    code=str(exc) if isinstance(exc,ValueError) and str(exc) in {"PATH_INVALID","FILES_TOO_LARGE","FILES_TOO_MANY","TOOL_INVALID"} else "REMOTE_FAILED"
    result={"error":code}
print(json.dumps(result,ensure_ascii=True,separators=(",",":")))
'''


class RailwayCodeSession:
    """One bounded project in a fresh VM, exported and destroyed on exit."""

    def __init__(self, *, graphql: Callable[[str, dict], dict] | None = None,
                 environment_id: str | None = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._graphql = graphql or _graphql_request
        self.environment_id = environment_id or os.environ.get("RAILWAY_ENVIRONMENT_ID", "")
        if not _ID.fullmatch(self.environment_id):
            raise CodeExecutionError("NOT_CONFIGURED")
        if graphql is None and not runtime_status()["configured"]:
            raise CodeExecutionError("NOT_CONFIGURED")
        self._sleep, self._clock = sleep, clock
        self.sandbox_id: str | None = None
        self._ready = False
        self._closed = False
        self._deadline = 0.0
        self._snapshot: list[dict[str, Any]] | None = None
        self.snapshot_truncated = False
        self.cleanup_status = {"confirmed": False, "status": "not_created"}
        self.tools: dict[str, bool] = {}
        self.phase = "create"

    def _data(self, query: str, variables: dict, field: str) -> dict:
        data = self._graphql(query, variables)
        if not isinstance(data, dict) or not isinstance(data.get(field), dict):
            raise CodeExecutionError("RESPONSE_INVALID")
        return data[field]

    def _vars(self) -> dict:
        return {"environmentId": self.environment_id, "id": self.sandbox_id}

    def __enter__(self) -> RailwayCodeSession:
        if self.sandbox_id or self._closed:
            raise CodeExecutionError("SESSION_INVALID")
        self._deadline = self._clock() + MAX_SESSION_SECONDS
        try:
            result = self._data(_CREATE, {"input": {
                "environmentId": self.environment_id, "idleTimeoutMinutes": 5,
                "networkIsolation": "ISOLATED", "resources": {"cpu": 1, "memoryGB": 1},
                "variables": {},
            }}, "sandboxCreate")
            sandbox_id = result.get("id")
            if not isinstance(sandbox_id, str) or not _ID.fullmatch(sandbox_id):
                raise CodeExecutionError("RESPONSE_INVALID")
            self.sandbox_id = sandbox_id
            self.cleanup_status = {"confirmed": False, "status": "created"}
            ready_deadline = self._clock() + 90
            while result.get("status") != "RUNNING":
                if result.get("status") != "CREATING":
                    raise CodeExecutionError("START_FAILED")
                if self._clock() >= ready_deadline:
                    raise CodeExecutionError("START_TIMEOUT")
                self._sleep(0.5)
                result = self._data(_GET, self._vars(), "sandbox")
            if result.get("networkIsolation") != "ISOLATED":
                raise CodeExecutionError("ISOLATION_FAILED")
            self._ready = True
            self.phase = "bootstrap"
            try:
                prepared = self._exec(_BOOTSTRAP, 180)
                if prepared["stdout"].strip() != "ready":
                    raise CodeExecutionError("TOOLS_MISSING")
            except CodeExecutionError as error:
                if error.code == "CODE_EXECUTION_REMOTE_FAILED":
                    raise CodeExecutionError("TOOLS_MISSING") from None
                raise
            self.phase = "preflight"
            preflight = self._remote({"action": "preflight"})
            self.tools = preflight.get("tools", {})
            # Only python3 is essential; other tools are reported, not required.
            if not isinstance(self.tools, dict) or self.tools.get("python3") is not True:
                raise CodeExecutionError("TOOLS_MISSING")
            return self
        except BaseException:
            self._destroy()
            raise

    def _exec(self, command: str, timeout: int) -> dict:
        if not self._ready or self._closed:
            raise CodeExecutionError("SESSION_INVALID")
        if self._clock() + timeout > self._deadline:
            raise CodeExecutionError("SESSION_TIMEOUT")
        if timeout > _DIRECT_EXEC_SECONDS:
            result = self._exec_detached(command, timeout)
        else:
            result = self._data(_EXEC, {**self._vars(), "command": command,
                                      "timeoutSec": timeout}, "sandboxExec")
        if result.get("timedOut"):
            raise CodeExecutionError("TIMEOUT")
        if result.get("truncated"):
            raise CodeExecutionError("RESPONSE_TOO_LARGE")
        if result.get("exitCode") != 0 or not isinstance(result.get("stdout"), str):
            raise CodeExecutionError("REMOTE_FAILED")
        return result

    def _exec_detached(self, command: str, timeout: int) -> dict:
        """Run a long command in the background and poll with short requests.

        One HTTPS request held open for minutes is cut by proxies in front of
        Railway (seen as a 5xx), so long work never rides a single request.
        """
        self._jobs = getattr(self, "_jobs", 0) + 1
        job = "/tmp/edurecover-job-%d" % self._jobs
        script = base64.b64encode(command.encode("utf-8")).decode("ascii")
        start = ("mkdir -p %s && printf %%s %s | base64 -d > %s/cmd && "
                 "S=$(command -v setsid || true); T=$(command -v timeout >/dev/null 2>&1 && echo 'timeout -s KILL %d' || true); "
                 "$S nohup /bin/sh -c \"$T /bin/sh %s/cmd > %s/out 2> %s/err; "
                 "echo \\$? > %s/rc.tmp; mv %s/rc.tmp %s/rc\" > /dev/null 2>&1 < /dev/null & "
                 "echo $! > %s/pid; echo started"
                 ) % (job, script, job, timeout, job, job, job, job, job, job, job)
        begun = self._data(_EXEC, {**self._vars(), "command": start, "timeoutSec": 30}, "sandboxExec")
        if begun.get("exitCode") != 0 or "started" not in str(begun.get("stdout")):
            raise CodeExecutionError("REMOTE_FAILED")
        started = self._clock()
        deadline = started + timeout + 15
        misses = 0
        while True:
            self._sleep(3.0)
            try:
                # Report the exit code, or "gone" when the background shell
                # died without writing one (killed or out of memory).
                probe = ("cat %s/rc 2>/dev/null && exit 0; sleep 1; cat %s/rc 2>/dev/null && exit 0; "
                         "kill -0 $(cat %s/pid 2>/dev/null) 2>/dev/null || echo gone") % (job, job, job)
                check = self._data(_EXEC, {**self._vars(), "command": probe,
                                           "timeoutSec": 20}, "sandboxExec")
            except CodeExecutionError as error:
                # Reading a status file is safe to repeat after a brief hiccup.
                misses += 1
                if error.code not in ("CODE_EXECUTION_SERVICE_UNAVAILABLE",
                                      "CODE_EXECUTION_TIMEOUT") or misses > 3:
                    raise
                check = {}
            code = str(check.get("stdout") or "").strip()
            if code == "gone":
                raise CodeExecutionError("PROCESS_LOST")
            if code:
                break
            if self._clock() >= deadline:
                return {"timedOut": True}
        try:
            exit_code = int(code)
        except ValueError:
            raise CodeExecutionError("RESPONSE_INVALID") from None
        if exit_code in (124, 137):
            # 137 is also an out-of-memory kill; only call it a timeout when
            # the time limit was actually reached.
            if self._clock() - started < timeout - 2:
                raise CodeExecutionError("PROCESS_LOST")
            return {"timedOut": True}
        out = self._data(_EXEC, {**self._vars(), "command": "head -c 15000 %s/out" % job,
                                 "timeoutSec": 20}, "sandboxExec")
        return {"exitCode": exit_code, "stdout": out.get("stdout") or "",
                "stderr": "", "truncated": bool(out.get("truncated")), "timedOut": False}

    def _remote(self, payload: dict, timeout: int = 15) -> dict:
        encoded = base64.b64encode(json.dumps(payload, ensure_ascii=True).encode()).decode()
        command = "env -i PATH=/usr/local/bin:/usr/bin:/bin HOME=/tmp/edurecover-home LANG=C.UTF-8 python3 -I -c " + shlex.quote(_REMOTE_HELPER) + " " + shlex.quote(encoded)
        result = self._exec(command, timeout)
        try:
            response = json.loads(result["stdout"])
            if not isinstance(response, dict):
                raise ValueError
        except (ValueError, TypeError):
            raise CodeExecutionError("RESPONSE_INVALID") from None
        if response.get("error"):
            code = response["error"]
            raise CodeExecutionError(code if code in {
                "PATH_INVALID", "FILES_TOO_LARGE", "FILES_TOO_MANY", "TOOL_INVALID"
            } else "REMOTE_FAILED")
        return response

    def execute(self, name: str, args: dict) -> dict:
        if not isinstance(args, dict):
            raise CodeExecutionError("INPUT_INVALID")
        if name == "file_list":
            path = _path(args.get("path", "."), directory=True)
            files, offset, truncated = [], 0, False
            while True:
                result = self._remote({"action": "list", "path": path, "offset": offset})
                page, count = result.get("files"), result.get("count")
                if (not isinstance(page, list) or type(count) is not int or not 0 <= count <= MAX_FILES
                        or len(page) > 16 or (not page and offset < count)):
                    raise CodeExecutionError("RESPONSE_INVALID")
                for item in page:
                    if not isinstance(item, dict):
                        raise CodeExecutionError("RESPONSE_INVALID")
                    _path(item.get("path"))
                    if type(item.get("bytes")) is not int or item["bytes"] < 0:
                        raise CodeExecutionError("RESPONSE_INVALID")
                files.extend(page)
                truncated = truncated or bool(result.get("truncated", False))
                offset += len(page)
                if offset >= count:
                    break
            return {"files": files, "truncated": truncated}
        if name == "file_read":
            path = _path(args.get("path"))
            chunks, size = [], 0
            while True:
                result = self._remote({"action": "read", "path": path, "offset": size})
                try:
                    chunk = base64.b64decode(result["data"], validate=True)
                    length = result["bytes"]
                    if result.get("exported") is False and type(length) is int and length > MAX_FILE_BYTES:
                        return {"path": path, "bytes": length, "exported": False, "reason": "file_size_limit"}
                    if (type(length) is not int or not 0 <= length <= MAX_FILE_BYTES
                            or len(chunk) > _CHUNK_BYTES or (not chunk and size < length)):
                        raise ValueError
                except (ValueError, TypeError, KeyError):
                    raise CodeExecutionError("RESPONSE_INVALID") from None
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise CodeExecutionError("FILES_TOO_LARGE")
                if size >= length:
                    break
            raw = b"".join(chunks)
            try:
                content, encoding = raw.decode("utf-8"), "utf-8"
            except UnicodeError:
                content, encoding = base64.b64encode(raw).decode("ascii"), "base64"
            return {"path": path, "content": content, "encoding": encoding,
                    "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "exported": True}
        if name == "file_write":
            path, content = _path(args.get("path")), args.get("content")
            if not isinstance(content, str):
                raise CodeExecutionError("INPUT_INVALID")
            try:
                encoding = args.get("encoding", "utf-8")
                if encoding not in ("utf-8", "base64"):
                    raise ValueError
                raw = base64.b64decode(content, validate=True) if encoding == "base64" else content.encode("utf-8")
            except (UnicodeError, ValueError):
                raise CodeExecutionError("INPUT_INVALID") from None
            if len(raw) > MAX_FILE_BYTES:
                raise CodeExecutionError("FILES_TOO_LARGE")
            self._snapshot = None
            for offset in range(0, max(1, len(raw)), _CHUNK_BYTES):
                chunk = raw[offset:offset + _CHUNK_BYTES]
                result = self._remote({"action": "write", "path": path, "offset": offset,
                                       "data": base64.b64encode(chunk).decode()})
                if result.get("path") != path or result.get("bytes") != offset + len(chunk):
                    raise CodeExecutionError("RESPONSE_INVALID")
            return {"path": path, "bytes": len(raw)}
        if name == "run_command":
            self.phase = "run"
            argv = args.get("argv")
            if (not isinstance(argv, list) or not 1 <= len(argv) <= 64
                    or any(not isinstance(item, str) or "\x00" in item or len(item) > 8192 for item in argv)
                    or not argv[0] or len(json.dumps(argv, ensure_ascii=True).encode()) > 16 * 1024):
                raise CodeExecutionError("COMMAND_INVALID")
            timeout = args.get("timeout_seconds", 30)
            if type(timeout) is not int or not 1 <= timeout <= MAX_COMMAND_SECONDS:
                raise CodeExecutionError("COMMAND_INVALID")
            cwd = _path(args.get("cwd", "."), directory=True)
            self._snapshot = None
            result = self._remote({"action": "run", "path": cwd, "argv": argv,
                                   "timeout_seconds": timeout}, timeout + 5)
            try:
                for key in ("stdout", "stderr"):
                    result[key] = base64.b64decode(result.pop(key + "_b64"), validate=True).decode("utf-8", "replace")
                if (result.get("exit_code") is not None and type(result["exit_code"]) is not int
                        or type(result.get("timed_out")) is not bool or type(result.get("truncated")) is not bool):
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise CodeExecutionError("RESPONSE_INVALID") from None
            return result
        raise CodeExecutionError("TOOL_INVALID")

    def seed(self, files: list[dict[str, Any]]) -> None:
        self.phase = "upload"
        if not isinstance(files, list) or len(files) > MAX_FILES:
            raise CodeExecutionError("FILES_TOO_MANY")
        total, paths = 0, set()
        for item in files:
            if isinstance(item, dict) and item.get("exported") is False:
                continue
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                raise CodeExecutionError("INPUT_INVALID")
            path = _path(item.get("path"))
            if path in paths:
                raise CodeExecutionError("INPUT_INVALID")
            paths.add(path)
            try:
                encoding = item.get("encoding", "utf-8")
                if encoding not in ("utf-8", "base64"):
                    raise ValueError
                size = len(base64.b64decode(item["content"], validate=True)) if encoding == "base64" else len(item["content"].encode("utf-8"))
            except (UnicodeError, ValueError):
                raise CodeExecutionError("INPUT_INVALID") from None
            if size > MAX_FILE_BYTES:
                raise CodeExecutionError("FILES_TOO_LARGE")
            total += size
            if total > MAX_TOTAL_BYTES:
                raise CodeExecutionError("FILES_TOO_LARGE")
        for item in files:
            if item.get("exported") is False:
                continue
            self.execute("file_write", item)

    def snapshot(self) -> list[dict[str, Any]]:
        if self._snapshot is not None:
            return [dict(item) for item in self._snapshot]
        self.phase = "export"
        listing = self.execute("file_list", {"path": "."})
        self.snapshot_truncated = listing["truncated"]
        saved, total = [], 0
        for item in listing["files"]:
            size = item["bytes"]
            if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
                saved.append({"path": item["path"], "bytes": size, "exported": False,
                              "reason": "file_size_limit" if size > MAX_FILE_BYTES else "total_size_limit"})
                continue
            exported = self.execute("file_read", {"path": item["path"]})
            size = exported["bytes"]
            if exported.get("exported") is True and total + size > MAX_TOTAL_BYTES:
                exported = {"path": item["path"], "bytes": size, "exported": False, "reason": "total_size_limit"}
            if exported.get("exported") is True:
                total += size
            saved.append(exported)
        self._snapshot = saved
        return [dict(item) for item in saved]

    def _destroy(self) -> None:
        if not self.sandbox_id or self.cleanup_status["confirmed"]:
            return
        try:
            # Railway may answer sandboxDestroy with null (already gone) and
            # the status query with null once the VM no longer exists.
            self.phase = "cleanup"
            try:
                data = self._graphql(_DESTROY, self._vars())
            except CodeExecutionError:
                # A refused destroy is fine when the VM is already gone.
                data = self._graphql(_GET, self._vars())
                gone = not isinstance(data, dict) or not data.get("sandbox") or \
                    data["sandbox"].get("status") in ("DESTROYED", "DESTROYING", "FAILED")
                if not gone:
                    raise
                data = {"sandboxDestroy": data.get("sandbox") if isinstance(data, dict) else None}
            if not isinstance(data, dict):
                raise CodeExecutionError("RESPONSE_INVALID")
            result = data.get("sandboxDestroy") or {"status": "DESTROYED"}
            deadline = self._clock() + 60
            while result.get("status") not in ("DESTROYED", "FAILED"):
                if result.get("status") not in ("DESTROYING", "RUNNING", "CREATING") \
                        or self._clock() >= deadline:
                    raise CodeExecutionError("CLEANUP_UNCONFIRMED")
                self._sleep(1.0)
                data = self._graphql(_GET, self._vars())
                if not isinstance(data, dict):
                    raise CodeExecutionError("RESPONSE_INVALID")
                result = data.get("sandbox") or {"status": "DESTROYED"}
            self.cleanup_status = {"confirmed": True, "status": "destroyed"}
            self._closed = True
        except BaseException:
            self.cleanup_status = {"confirmed": False, "status": "cleanup_unconfirmed"}
            raise CodeExecutionError("CLEANUP_UNCONFIRMED") from None

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        # When the run already failed, report that first cause; a cleanup
        # problem is still kept in cleanup_status instead of hiding it.
        try:
            if self._ready and self._snapshot is None:
                self.snapshot()
        except BaseException:
            if exc_type is None:
                self._destroy_keep_first()
                raise
        finally:
            if exc_type is not None:
                self._destroy_keep_first()
        if exc_type is None:
            self._destroy()
        return False

    def _destroy_keep_first(self) -> None:
        try:
            self._destroy()
        except CodeExecutionError:
            pass
