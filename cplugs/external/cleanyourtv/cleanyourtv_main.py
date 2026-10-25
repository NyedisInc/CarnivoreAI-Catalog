"""The cleanyourtv entry: owner routes for the panel view (every change to a TV), and two read-only tools for agents (list, last checks).

Long steps (apply, reboot, checks, restore) run as jobs: the route waits up to JOB_WAIT_S for a quick answer or refusal, else returns a job id the view polls.
Trust: `unrestricted` in cplug.json, so this process opens the TV's ADB port itself (vendored adb_shell, pure Python). Upstream: github.com/Gathin23/cleanyourtv (MIT) at 204143007f.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))  # vendored adb_shell, rsa, pyasn1 sit beside this file

from cplug_sdk import Plugin  # noqa: E402

import cyt_adb  # noqa: E402
from cyt_flow import Manager, Refusal  # noqa: E402
from cyt_store import Store  # noqa: E402

JOB_WAIT_S = 2.0
MAX_JOBS = 50
GLANCE_DOC = {"schema": 1, "root": {"type": "text", "value": "clean your tv", "tone": "muted"}}


class TvPlugin(Plugin):
    """The SDK plugin plus `ui.render` for the one surface the host asks of every running plugin (the bar glance): a document with no badge, so nothing is drawn and the host's poll gets an answer, not a 502."""

    async def handle(self, method: str, params: Dict[str, Any]) -> Any:
        """Host requests: `ui.render` for glance here, everything else the SDK's. Inputs: method; params. Output: the result. Raises KeyError for another surface."""
        if method == "ui.render":
            if params.get("surface") != "glance":
                raise KeyError(params.get("surface"))
            return {"document": GLANCE_DOC}
        return await super().handle(method, params)


plugin = TvPlugin()
_manager: Optional[Manager] = None
_jobs: Dict[str, Dict[str, Any]] = {}


def data_dir() -> Path:
    """The plugin's own data folder: CYT_DATA_DIR, else ./cyt-data under the process folder (the plugin's files/). Never the home-folder android key."""
    return Path(os.environ.get("CYT_DATA_DIR") or (Path.cwd() / "cyt-data"))


def manager() -> Manager:
    """The one Manager with the real ADB adapters (built on first use)."""
    global _manager
    if _manager is None:
        _manager = Manager(Store(data_dir()), connect=lambda tv, key: cyt_adb.AdbTcpDevice(tv["ip"], tv["port"], key), probe=cyt_adb.probe_port,
                           verify_revoked=cyt_adb.verify_revoked, make_key=cyt_adb.make_key)
    return _manager


def set_manager(m: Optional[Manager]) -> None:
    """Swap the Manager (tests)."""
    global _manager
    _manager = m


def _ok(**body: Any) -> Dict[str, Any]:
    return {"status": 200, "body": {"ok": True, **body}}


def _refused(exc: Refusal) -> Dict[str, Any]:
    """A refusal as HTTP 200 with ok:false: the view bridge turns any non-2xx into a bare "failed", losing the reason."""
    return {"status": 200, "body": {"ok": False, "code": exc.code, "message": exc.message, **exc.extra}}


async def _job(fn: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Run a blocking step off the loop. Quick answers and refusals return at once; slow ones return {job} to poll."""
    jid = uuid.uuid4().hex[:12]
    rec = {"id": jid, "state": "running"}
    _jobs[jid] = rec
    while len(_jobs) > MAX_JOBS:
        _jobs.pop(next(iter(_jobs)))

    async def run() -> Dict[str, Any]:
        try:
            out = _ok(**await asyncio.to_thread(fn))
        except Refusal as exc:
            out = _refused(exc)
        except Exception as exc:  # noqa: BLE001  report to the owner, never crash the plugin
            await plugin.host.log("error", "cyt_step_failed", error=type(exc).__name__)
            out = {"status": 200, "body": {"ok": False, "code": "internal", "message": f"unexpected error ({type(exc).__name__})"}}
        rec.update(state="done", result=out)
        return out

    task = asyncio.get_running_loop().create_task(run())
    done, _ = await asyncio.wait({task}, timeout=JOB_WAIT_S)
    return task.result() if done else {"status": 200, "body": {"ok": True, "job": jid, "running": True}}


def _arg(req: Dict[str, Any], key: str, default: Any = "") -> Any:
    body = req.get("body")
    return body.get(key, default) if isinstance(body, dict) else default


@plugin.route("state")
async def r_state(req: Dict[str, Any]) -> Dict[str, Any]:
    """All TVs and the plan. Read only."""
    m = manager()
    return _ok(tvs=[m.view(t) for t in m.s.all()], plan=m.plan())


@plugin.route("job")
async def r_job(req: Dict[str, Any]) -> Dict[str, Any]:
    """A job's state and, when done, its route answer."""
    rec = _jobs.get(str(_arg(req, "id")))
    return _ok(job=rec) if rec else {"status": 200, "body": {"ok": False, "code": "no_job", "message": "no such job"}}


def _simple(name: str, call: Callable[[Manager, Dict[str, Any]], Dict[str, Any]]) -> None:
    @plugin.route(name)
    async def handler(req: Dict[str, Any]) -> Dict[str, Any]:
        try:
            m = manager()
            return await _job(lambda: call(m, req))
        except Refusal as exc:
            return _refused(exc)


_simple("add", lambda m, r: {"tv": m.add_tv(str(_arg(r, "ip")), str(_arg(r, "name")))})
_simple("remove", lambda m, r: (m.remove_tv(str(_arg(r, "tv"))), {})[1])
_simple("pair", lambda m, r: {"tv": m.pair(str(_arg(r, "tv")))})
_simple("batch", lambda m, r: m.apply_batch(str(_arg(r, "tv")), str(_arg(r, "batch"))))
_simple("reboot", lambda m, r: m.reboot(str(_arg(r, "tv"))))
_simple("checks", lambda m, r: m.run_checks(str(_arg(r, "tv"))))
_simple("restore", lambda m, r: m.restore(str(_arg(r, "tv"))))
_simple("setupwraith", lambda m, r: m.setupwraith(str(_arg(r, "tv")), _arg(r, "cast_will_stop", False) is True))
_simple("closeout", lambda m, r: m.closeout(str(_arg(r, "tv")), _arg(r, "revoked_on_tv", False) is True, _arg(r, "debugging_off", False) is True))


@plugin.tool("tv_status", "Each added TV: identity, whether adb is open, applied batches, lock state and last check outcome. Read only.",
             {"type": "object", "properties": {}, "additionalProperties": False}, gate="read")
async def t_status(args: Dict[str, Any], caller: Any) -> str:
    """List TVs. Output: JSON text."""
    m = manager()
    return json.dumps([{k: v for k, v in m.view(t).items() if k not in ("journal",)} | {"checks": (t.get("checks") or {}).get("passed")} for t in m.s.all()])


@plugin.tool("tv_checks", "The last post-reboot check results for one TV. Read only.",
             {"type": "object", "properties": {"tv": {"type": "string"}}, "required": ["tv"], "additionalProperties": False}, gate="read")
async def t_checks(args: Dict[str, Any], caller: Any) -> str:
    """Last checks of one TV. Output: JSON text."""
    tv = manager().s.load(str(args.get("tv", "")))
    return json.dumps((tv or {}).get("checks") or {"error": "no such tv or no checks yet"})
