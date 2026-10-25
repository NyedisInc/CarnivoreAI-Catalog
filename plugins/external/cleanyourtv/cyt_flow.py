"""The state machine: add, pair, batch, settle, reboot, checks, restore, setupwraith, close-out. Every hard rule is a Refusal raised here.

Rules (each has a test): no reboot until SETTLE_S after the last package change, then `sync`; the next batch (and setupwraith) stay locked
until the post-reboot checks pass; setupwraith is its own gated step; restore replays the undo record exactly; close-out runs in order.
Commands sent to a TV are the fixed templates below with a validated package name. There is no uninstall and no free shell.
"""

from __future__ import annotations

import ipaddress
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import cyt_guards as G
from cyt_adb import ADB_PORT, DeviceError
from cyt_store import Store

SETTLE_S = 75
CAST_PORT = 8009
CAST_CLOSE = ("am force-stop com.google.android.apps.mediashell",
              "am start-foreground-service -n com.google.android.apps.mediashell/.CastReceiverService")  # REVIEW.md M1 fix
SCALES = ("window_animation_scale", "transition_animation_scale", "animator_duration_scale")
HOME_Q = "cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME"
CLAIM_Q = "cmd package query-activities --brief -a android.intent.action.MAIN -c android.intent.category.HOME"
HANDLER_Q = ("cmd package query-activities --brief -a android.intent.action.VIEW -t vnd.android.cursor.item/channel 2>/dev/null; echo ==; "
             "cmd package query-activities --brief -a android.settings.SETTINGS 2>/dev/null; true")
HOME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+/[A-Za-z0-9_.$]+$")
SCALE_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")
KNOWN_LAUNCHERS = ("com.google.android.tvlauncher", "com.google.android.apps.tv.launcherx", "com.google.android.leanbacklauncher", "com.mitv.tvhome")
NOT_HOME = ("com.google.android.tungsten.setupwraith", "com.android.tv.settings", "com.android.settings", "com.android.provision")
LIST_FILES = {"vh21": "vh21.txt", "vh21-eu": "vh21-eu.txt"}
CLOSEOUT_STEPS = ("revoked", "revoke_verified", "port_closed", "key_deleted")


class Refusal(Exception):
    """A hard rule said no. code is a stable slug; message is plain text for the owner; extra carries numbers (e.g. remaining seconds)."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


def _pkgs(text: str) -> List[str]:
    """Package names from `pm list packages` output."""
    return [ln.split(":", 1)[1].strip() for ln in text.replace("\r", "").splitlines() if ln.startswith("package:")]


def connection_type(connectivity: str) -> Optional[str]:
    """The connected transport from `dumpsys connectivity`. Output: 'ethernet', 'wifi', 'both' or None."""
    lines = [ln for ln in connectivity.splitlines() if "NetworkAgentInfo" in ln and "CONNECTED" in ln]
    eth, wifi = any("ETHERNET" in ln for ln in lines), any("WIFI" in ln for ln in lines)
    return "both" if eth and wifi else "ethernet" if eth else "wifi" if wifi else None


def wifi_owners(dumpsys_wifi: str) -> Tuple[bool, List[str]]:
    """(readable, owners of saved networks) from `dumpsys wifi`. Unreadable (no 'Wi-Fi is ' marker) fails closed upstream and here."""
    return ("Wi-Fi is " in dumpsys_wifi, re.findall(r"cname=([^ \n]*)", dumpsys_wifi))


class Manager:
    """All TV operations. Inputs: store; connect (tv, key_path -> device context manager); probe (ip, port -> open|closed|unknown);
    verify_revoked (ip, port, key_path -> bool|None); make_key (key_path -> None); clock; sleep."""

    def __init__(self, store: Store, connect: Callable, probe: Callable, verify_revoked: Callable, make_key: Callable,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep) -> None:
        self.s, self._connect, self.probe, self._verify, self._make_key, self.clock, self.sleep = store, connect, probe, verify_revoked, make_key, clock, sleep
        self._busy: Dict[str, threading.Lock] = {}

    # ---- plumbing -------------------------------------------------------------------------------------------------
    def _tv(self, tv_id: str) -> Dict[str, Any]:
        tv = self.s.load(tv_id)
        if not tv:
            raise Refusal("unknown_tv", "no such tv")
        return tv

    @contextmanager
    def _lock(self, tv_id: str) -> Iterator[None]:
        """One operation per TV at a time (a second click is refused, never queued)."""
        lock = self._busy.setdefault(tv_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise Refusal("busy", "another step is running on this tv")
        try:
            yield
        finally:
            lock.release()

    @contextmanager
    def _dev(self, tv: Dict[str, Any], need_identity: bool = True) -> Iterator[Any]:
        """A connected device whose measured identity matches the record (a different device at the same IP is refused)."""
        if not self.s.key_exists(tv["id"]):
            raise Refusal("no_key", "this tv has no key; pair it first")
        try:
            ctx = self._connect(tv, self.s.key_path(tv["id"]))
            dev = ctx.__enter__()
        except DeviceError as exc:
            raise Refusal("unreachable", str(exc)) from exc
        try:
            if need_identity and tv.get("identity"):
                ident = self._identity(dev)
                if (ident["serial"], ident["android_id"]) != (tv["identity"]["serial"], tv["identity"]["android_id"]):
                    raise Refusal("wrong_device", "a different device answers at this address; nothing was changed")
            yield dev
        except DeviceError as exc:
            raise Refusal("device_error", str(exc)) from exc
        finally:
            ctx.__exit__(None, None, None)

    @staticmethod
    def _identity(dev: Any) -> Dict[str, str]:
        g = lambda c: dev.shell(c).strip()  # noqa: E731
        return {"serial": g("getprop ro.serialno"), "android_id": g("settings get secure android_id"), "model": g("getprop ro.product.model"),
                "fingerprint": g("getprop ro.build.fingerprint"), "adb_secure": g("getprop ro.adb.secure")}

    def _stamp_write(self, tv: Dict[str, Any], why: str) -> None:
        tv["last_write_at"], tv["locked"], tv["lock_reason"] = self.clock(), True, why
        tv["synced_at"] = None
        self.s.save(tv)

    def view(self, tv: Dict[str, Any]) -> Dict[str, Any]:
        """A TV record as the UI/tools see it (no key material; the settle countdown computed). Output: dict."""
        last = tv.get("last_write_at")
        remaining = max(0, int(SETTLE_S - (self.clock() - last) + 0.999)) if last else 0
        keep = ("id", "name", "ip", "port", "identity", "paired", "applied", "ours", "locked", "lock_reason", "checks", "cast_dropped",
                "adb_open", "closeout", "restoring", "journal", "last_write_at", "rebooted_at")
        out = {k: tv.get(k) for k in keep}
        out.update(settle_remaining=remaining, has_baseline=bool(tv.get("baseline")), has_undo=bool(tv.get("undo")), key_present=self.s.key_exists(tv["id"]))
        return out

    # ---- add, pair ------------------------------------------------------------------------------------------------
    def add_tv(self, ip: str, name: str = "") -> Dict[str, Any]:
        """Add a TV by IPv4 address. Only exact private LAN addresses; no scan, no discovery. Output: the TV view."""
        try:
            addr = ipaddress.ip_address(ip.strip())
        except ValueError as exc:
            raise Refusal("bad_ip", "that is not an ip address") from exc
        if addr.version != 4 or not addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_unspecified:
            raise Refusal("bad_ip", "only a private lan ipv4 address of the tv is accepted")
        tv_id = "tv_" + str(addr).replace(".", "_")
        if self.s.load(tv_id):
            raise Refusal("exists", "this tv is already added")
        tv = {"id": tv_id, "ip": str(addr), "port": ADB_PORT, "name": (name.strip() or str(addr))[:40], "paired": False, "applied": [], "ours": [],
              "undo": {}, "journal": [], "locked": False, "checks": None, "closeout": {}, "adb_open": False}
        self.s.save(tv)
        return self.view(tv)

    def remove_tv(self, tv_id: str) -> None:
        """Forget a TV record. Refused while ADB may still be open on it (close-out first)."""
        tv = self._tv(tv_id)
        if tv.get("adb_open"):
            raise Refusal("adb_open", "adb is still open on this tv; run close-out first")
        self.s.delete_key(tv_id)
        self.s.remove(tv_id)

    def pair(self, tv_id: str) -> Dict[str, Any]:
        """Mint this TV's own key, connect (the TV asks 'allow usb debugging?'; tap allow, not always allow), measure identity, take the baseline once."""
        tv = self._tv(tv_id)
        with self._lock(tv_id):
            self._make_key(self.s.key_path(tv_id))
            with self._dev(tv, need_identity=False) as dev:
                ident = self._identity(dev)
                if not ident["serial"] or not ident["android_id"]:
                    raise Refusal("no_identity", "could not read the tv's serial and android id, so it cannot be told apart from another device")
                if tv.get("identity") and (tv["identity"]["serial"], tv["identity"]["android_id"]) != (ident["serial"], ident["android_id"]):
                    raise Refusal("wrong_device", "a different device answers at this address")
                tv["identity"], tv["paired"], tv["paired_at"], tv["adb_open"] = ident, True, self.clock(), True
                tv["closeout"] = {}
                if not tv.get("baseline"):
                    tv["baseline"] = self._baseline(dev)
                self.s.save(tv)
        return self.view(tv)

    def _baseline(self, dev: Any) -> Dict[str, Any]:
        """The TV's state before any change. Written once, never overwritten. Output: dict."""
        allp = _pkgs(dev.shell("pm list packages"))
        if not allp:
            raise Refusal("no_packages", "the tv returned no packages; nothing was recorded")
        disabled, enabled = sorted(_pkgs(dev.shell("pm list packages -d"))), sorted(_pkgs(dev.shell("pm list packages -e")))
        home, handlers = self._home(dev), self._handlers(dev)
        keep = [p for p in enabled if G.refusal_reason(p) == "on the must-stay list"] + [p for p in handlers if p in enabled]
        return {"taken_at": self.clock(), "packages": sorted(allp), "disabled": disabled, "enabled": enabled, "home": home,
                "claimants": self._claimants(dev), "scales": {k: dev.shell(f"settings get global {k}").strip() for k in SCALES},
                "connection": connection_type(dev.shell("dumpsys connectivity")), "wifi_owners": wifi_owners(dev.shell("dumpsys wifi"))[1],
                "keep_enabled": sorted(set(keep)), "setupwraith": "absent" if G.SETUPWRAITH not in allp else "disabled" if G.SETUPWRAITH in disabled else "enabled"}

    @staticmethod
    def _home(dev: Any) -> Optional[str]:
        ans = dev.shell(HOME_Q).replace("\r", "").strip().splitlines()
        cand = ans[-1].strip() if ans else ""
        return cand if HOME_RE.fullmatch(cand) else None

    @staticmethod
    def _claimants(dev: Any) -> List[str]:
        raw = dev.shell(CLAIM_Q).replace("\r", "")
        return sorted(set(re.findall(r"^\s*([A-Za-z0-9_.]+/[A-Za-z0-9_.$]+)", raw, re.M)))

    @staticmethod
    def _handlers(dev: Any) -> List[str]:
        """Apps that answer the input-channel VIEW intent or Settings; they must stay enabled. Raises DeviceError if the TV cannot say."""
        raw = dev.shell(HANDLER_Q).replace("\r", "")
        return sorted(set(re.findall(r"^\s*([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)/", raw, re.M)))

    # ---- plan, batch ----------------------------------------------------------------------------------------------
    def plan(self) -> Dict[str, Any]:
        """The vendored lists as numbered batches, guards applied (pure; no TV). Output: {list id: {batches, refused}}."""
        return {lid: G.plan_batches(lid, (G.DATA / "lists" / fn).read_text()) for lid, fn in LIST_FILES.items()}

    def _batch(self, batch_id: str) -> List[str]:
        lid = batch_id.split(":", 1)[0]
        for b in self.plan().get(lid, {}).get("batches", []):
            if b["id"] == batch_id:
                return list(b["packages"])
        raise Refusal("unknown_batch", "no such batch")

    def _device_guards(self, dev: Any, pkgs: List[str], baseline: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, str]]]:
        """Drop packages the TV itself says must stay: Home owner with no other launcher, input drivers, input/settings handlers. Fails closed."""
        skipped: List[Dict[str, str]] = []
        keep = list(pkgs)

        def drop(p: str, why: str) -> None:
            if p in keep:
                keep.remove(p)
                skipped.append({"package": p, "reason": why})

        owner_raw = dev.shell(HOME_Q).replace("\r", "").strip().splitlines()
        owner = owner_raw[-1].strip().split("/")[0] if owner_raw else ""
        claim_pk = {c.split("/")[0] for c in self._claimants(dev) if not c.endswith("FallbackHome") and "factory" not in c.lower()} - set(NOT_HOME)
        if owner and owner != "android":
            if owner in keep and not (claim_pk - {owner} - set(keep)):
                drop(owner, "owns Home and nothing else could answer Home")
        elif claim_pk:
            for p in [p for p in keep if p in claim_pk]:
                if not (claim_pk - set(keep)):
                    drop(p, "Home has no default and no other launcher would remain")
        else:
            for p in [p for p in keep if p in KNOWN_LAUNCHERS]:
                drop(p, "could not ask the tv which apps answer Home, and this is a stock launcher")
        if keep:  # TV_INPUT_HARDWARE: one round trip, as upstream disable.sh
            names = " ".join(keep)
            out = dev.shell(f"for p in {names}; do dumpsys package $p | sed -n -E '/requested permissions:/,/(install|runtime) permissions:|User [0-9]+:/p' | grep -q TV_INPUT_HARDWARE && echo $p; done; true")
            for p in out.replace("\r", "").split():
                drop(p, "asks for TV_INPUT_HARDWARE: the software layer under the hdmi/av/tuner inputs")
        for p in self._handlers(dev):
            drop(p, "the tv opens its inputs or settings with it")
        return keep, skipped

    def apply_batch(self, tv_id: str, batch_id: str) -> Dict[str, Any]:
        """Disable one batch (pm disable-user, never uninstall). Refused while locked. Writes the undo record before the first command."""
        tv = self._tv(tv_id)
        with self._lock(tv_id):
            tv = self._tv(tv_id)
            if not tv.get("paired") or not tv.get("baseline"):
                raise Refusal("not_paired", "pair this tv first")
            if tv.get("locked"):
                raise Refusal("locked", f"locked: {tv.get('lock_reason') or 'the last change'}; reboot and pass the checks first")
            pkgs = self._batch(batch_id)
            if batch_id in tv["applied"]:
                raise Refusal("already_applied", "this batch was already applied")
            if any(G.refusal_reason(p) for p in pkgs):  # defence in depth: the list is data, re-check at the door
                raise Refusal("guard", "a package in this batch is on a refusal list")
            with self._dev(tv) as dev:
                allp, dis = set(_pkgs(dev.shell("pm list packages"))), set(_pkgs(dev.shell("pm list packages -d")))
                skipped = [{"package": p, "reason": "not installed on this tv"} for p in pkgs if p not in allp]
                skipped += [{"package": p, "reason": "already disabled before"} for p in pkgs if p in dis]
                todo = [p for p in pkgs if p in allp and p not in dis]
                todo, more = self._device_guards(dev, todo, tv["baseline"])
                skipped += more
                if not todo:
                    raise Refusal("nothing_to_do", "every package in this batch was skipped", skipped=skipped)
                for p in todo:  # undo first, on disk, before the first command (first write wins)
                    tv["undo"].setdefault(p, "enabled")
                for p in pkgs:
                    tv["undo"].setdefault(p, "absent" if p not in allp else "disabled" if p in dis else "enabled")
                self.s.save(tv)
                for p in todo:
                    dev.shell(f"pm disable-user --user 0 {p}")
                self._stamp_write(tv, f"batch {batch_id} applied; reboot and checks pending")
                now_dis = set(_pkgs(dev.shell("pm list packages -d")))
            done = [p for p in todo if p in now_dis]
            tv["ours"] = sorted(set(tv["ours"]) | set(done))
            tv["applied"].append(batch_id)
            tv["journal"].append({"batch": batch_id, "at": tv["last_write_at"], "disabled": done, "skipped": skipped, "failed": [p for p in todo if p not in now_dis]})
            self.s.save(tv)
        return {"disabled": done, "skipped": skipped, "failed": tv["journal"][-1]["failed"], "tv": self.view(tv)}

    # ---- reboot, checks -------------------------------------------------------------------------------------------
    def reboot(self, tv_id: str) -> Dict[str, Any]:
        """Reboot, but only SETTLE_S after the last package change, and only after `sync`. Refused with the seconds left otherwise."""
        tv = self._tv(tv_id)
        with self._lock(tv_id):
            tv = self._tv(tv_id)
            if not tv.get("paired") or not tv.get("baseline"):
                raise Refusal("not_paired", "pair this tv first")
            last = tv.get("last_write_at")
            if last is not None and self.clock() - last < SETTLE_S:
                left = int(SETTLE_S - (self.clock() - last) + 0.999)
                raise Refusal("settle", f"wait {left} more seconds: disables persist lazily, and a reboot too soon reverts them", remaining=left)
            with self._dev(tv) as dev:
                dev.shell("sync")
                if self._tv(tv_id).get("last_write_at") != last:  # nothing writes between the settle check and the reboot
                    raise Refusal("write_during_sync", "a change landed during sync; wait again")
                dev.reboot()
            tv["synced_at"], tv["rebooted_at"] = self.clock(), self.clock()
            self.s.save(tv)
        return {"rebooted": True, "tv": self.view(tv)}

    def _wait_back(self, tv: Dict[str, Any], tries: int = 60, gap_s: float = 5.0) -> Any:
        """Poll until ADB answers after a reboot. Output: the connect context manager entered, or raises Refusal."""
        for i in range(tries):
            try:
                ctx = self._connect(tv, self.s.key_path(tv["id"]))
                return ctx, ctx.__enter__()
            except DeviceError:
                if i + 1 < tries:
                    self.sleep(gap_s)
        return None, None

    def run_checks(self, tv_id: str) -> Dict[str, Any]:
        """The post-reboot gate. Eight checks; 'could not determine' blocks like a failure. Passing unlocks the next batch."""
        tv = self._tv(tv_id)
        with self._lock(tv_id):
            tv = self._tv(tv_id)
            if not tv.get("baseline"):
                raise Refusal("not_paired", "pair this tv first")
            last = tv.get("last_write_at")
            if last is not None and (tv.get("rebooted_at") or 0) < last:
                raise Refusal("reboot_first", "the checks run after a reboot that follows the last change; reboot first")
            R: List[Dict[str, str]] = []
            add = lambda n, s, d="": R.append({"name": n, "status": s, "detail": d})  # noqa: E731
            ctx, dev = self._wait_back(tv)
            if dev is None:
                add("adb back", "fail", "the tv did not come back on adb")
                return self._finish_checks(tv, R)
            try:
                self._checks_on(tv, dev, add)
            except DeviceError as exc:
                add("checks", "unknown", f"link lost: {exc}")
            finally:
                ctx.__exit__(None, None, None)
            return self._finish_checks(tv, R)

    def _checks_on(self, tv: Dict[str, Any], dev: Any, add: Callable) -> None:
        b = tv["baseline"]
        ident = self._identity(dev)
        same = (ident["serial"], ident["android_id"]) == (tv["identity"]["serial"], tv["identity"]["android_id"])
        boot = dev.shell("getprop sys.boot_completed").strip()
        add("adb back", "pass" if same and boot == "1" else "fail", "" if same and boot == "1" else ("different device" if not same else f"boot_completed={boot or '?'}"))
        conn = connection_type(dev.shell("dumpsys connectivity"))
        add("network up", "pass" if conn else "fail", conn or "no connected wifi or ethernet")
        if tv.get("cast_dropped"):
            add("cast 8009", "skipped", "cast was dropped on purpose (setupwraith step)")
        else:
            port = self.probe(tv["ip"], CAST_PORT)
            proc = dev.shell("pidof com.google.android.apps.mediashell").strip()
            add("cast 8009", "pass" if port == "open" and proc else "fail" if port == "closed" or not proc else "unknown", f"port {port}, mediashell {'running' if proc else 'not running'}")
        en = set(_pkgs(dev.shell("pm list packages -e")))
        miss = [p for p in ("com.android.vending", "com.google.android.gms") if p not in en]
        add("play store", "pass" if not miss else "fail", ", ".join(miss) + (" not enabled" if miss else ""))
        dev.shell("am start -a android.settings.SETTINGS")
        self.sleep(4)
        top = next((ln for ln in dev.shell("dumpsys activity activities").splitlines() if "mResumedActivity" in ln or "topResumedActivity" in ln), "")
        dev.shell("input keyevent KEYCODE_HOME")
        add("settings opens", "pass" if ("com.android.tv.settings" in top or "com.android.settings" in top) else "fail" if top else "unknown", top.strip()[:100])
        lost = [p for p in b.get("keep_enabled", []) if p not in en]
        add("input apps enabled", "pass" if not lost else "fail", ", ".join(lost))
        home = self._home(dev)
        add("home resolves", "pass" if home == b.get("home") else "fail", f"{home or 'none'} (expected {b.get('home') or 'none'})")
        now_dis = set(_pkgs(dev.shell("pm list packages -d")))
        want = set(b.get("disabled", [])) | set(tv.get("ours", []))
        reverted, extra = sorted(set(tv.get("ours", [])) - now_dis), sorted(now_dis - want)
        add("nothing reverted", "pass" if not reverted and not extra else "fail", ("reverted: " + ", ".join(reverted) + ". " if reverted else "") + ("changed by something else: " + ", ".join(extra[:10]) if extra else ""))

    def _finish_checks(self, tv: Dict[str, Any], results: List[Dict[str, str]]) -> Dict[str, Any]:
        passed = bool(results) and all(r["status"] in ("pass", "skipped") for r in results) and len(results) >= 8
        tv["checks"] = {"at": self.clock(), "passed": passed, "results": results}
        if passed:
            tv["locked"], tv["lock_reason"] = False, ""
            if tv.get("restoring"):
                tv.update(undo={}, ours=[], applied=[], restoring=False, cast_dropped=False)
        self.s.save(tv)
        return {"passed": passed, "results": results, "tv": self.view(tv)}

    # ---- restore --------------------------------------------------------------------------------------------------
    def restore(self, tv_id: str) -> Dict[str, Any]:
        """Replay the undo record exactly: re-enable what we disabled, leave what was already disabled, put Home and animation scales back."""
        tv = self._tv(tv_id)
        with self._lock(tv_id):
            tv = self._tv(tv_id)
            if not tv.get("undo"):
                raise Refusal("no_record", "there is no undo record for this tv, so nothing can be restored exactly")
            b = tv["baseline"]
            with self._dev(tv) as dev:
                for p, was in sorted(tv["undo"].items()):
                    if was == "enabled":
                        dev.shell(f"pm enable {p}")
                if tv["undo"].get(G.SETUPWRAITH) == "enabled":
                    for c in CAST_CLOSE:
                        dev.shell(c)
                if b.get("home") and self._home(dev) != b["home"]:
                    dev.shell(f"cmd package set-home-activity --user 0 {b['home']}")
                for k, v in b.get("scales", {}).items():
                    if dev.shell(f"settings get global {k}").strip() != v:
                        if v == "null":
                            dev.shell(f"settings delete global {k}")
                        elif SCALE_RE.fullmatch(v):
                            dev.shell(f"settings put global {k} {v}")
                self._stamp_write(tv, "restore applied; reboot and checks pending")
                dis = set(_pkgs(dev.shell("pm list packages -d")))
            wrong = [p for p, was in tv["undo"].items() if (was == "enabled" and p in dis) or (was == "disabled" and p not in dis)]
            tv["restoring"] = True
            tv["ours"] = []
            tv["journal"].append({"restore": True, "at": tv["last_write_at"], "not_restored": wrong})
            self.s.save(tv)
        return {"not_restored": wrong, "tv": self.view(tv)}

    # ---- setupwraith ----------------------------------------------------------------------------------------------
    def setupwraith(self, tv_id: str, cast_will_stop: bool) -> Dict[str, Any]:
        """The setupwraith step: its own action, gated by what dumpsys shows. This is the whole of FORCE_SETUPWRAITH: it never overrides the gate."""
        tv = self._tv(tv_id)
        with self._lock(tv_id):
            tv = self._tv(tv_id)
            if not tv.get("paired") or not tv.get("baseline"):
                raise Refusal("not_paired", "pair this tv first")
            if tv.get("locked"):
                raise Refusal("locked", "locked: reboot and pass the checks first")
            if not cast_will_stop:
                raise Refusal("needs_tick", "tick that google cast will stop working; disabling setupwraith removes the cast receiver's credentials")
            if tv["baseline"].get("setupwraith") != "enabled" or tv["undo"].get(G.SETUPWRAITH) == "enabled":
                raise Refusal("nothing_to_do", "setupwraith is not installed and enabled on this tv")
            with self._dev(tv) as dev:
                readable, owners = wifi_owners(dev.shell("dumpsys wifi"))
                if not readable:
                    raise Refusal("wifi_unreadable", "could not read the saved wifi networks from the tv; refusing rather than risk dropping wifi")
                conn = connection_type(dev.shell("dumpsys connectivity"))
                if conn == "ethernet":
                    if owners:
                        raise Refusal("gate_ethernet", "on ethernet this needs no saved wifi networks: forget them on the tv, then try again", owners=owners)
                elif conn in ("wifi", "both"):
                    mine = [o for o in owners if "setupwraith" in o]
                    if mine or not owners:
                        raise Refusal("gate_wifi", "a saved wifi network belongs to setupwraith (or none could be read): forget it and rejoin on the tv, then try again", owners=owners)
                else:
                    raise Refusal("gate_unknown", "could not tell whether the tv is on ethernet or wifi")
                tv["undo"].setdefault(G.SETUPWRAITH, "enabled")
                self.s.save(tv)
                dev.shell(f"pm disable-user --user 0 {G.SETUPWRAITH}")
                self._stamp_write(tv, "setupwraith disabled; reboot and checks pending")
                done = G.SETUPWRAITH in set(_pkgs(dev.shell("pm list packages -d")))
            if done:
                tv["ours"] = sorted(set(tv["ours"]) | {G.SETUPWRAITH})
                tv["cast_dropped"] = True
            tv["journal"].append({"setupwraith": True, "at": tv["last_write_at"], "connection": conn, "disabled": done})
            self.s.save(tv)
        return {"disabled": done, "tv": self.view(tv)}

    # ---- close-out ------------------------------------------------------------------------------------------------
    def closeout(self, tv_id: str, revoked_on_tv: bool = False, debugging_off: bool = False) -> Dict[str, Any]:
        """Close-out, always allowed, strictly in order: revoke, verify revoke, verify port closed, delete this TV's key. Stops at the first failing step."""
        tv = self._tv(tv_id)
        with self._lock(tv_id):
            tv = self._tv(tv_id)
            co = tv.setdefault("closeout", {})
            steps: List[Dict[str, str]] = []

            def step(name: str, ok: bool, detail: str) -> bool:
                co[name] = ok
                steps.append({"step": name, "status": "pass" if ok else "fail", "detail": detail})
                self.s.save(tv)
                return ok

            if not co.get("revoked"):
                if not step("revoked", bool(revoked_on_tv), "you revoked usb debugging authorizations on the tv" if revoked_on_tv else "on the tv: settings, developer options, revoke usb debugging authorizations, then tick the box"):
                    return {"steps": steps, "done": False, "tv": self.view(tv)}
            if not co.get("revoke_verified"):
                if not self.s.key_exists(tv_id):
                    step("revoke_verified", False, "no key left to test with; if you revoked on the tv it is closed to this key")
                    return {"steps": steps, "done": False, "tv": self.view(tv)}
                res = self._verify(tv["ip"], tv["port"], self.s.key_path(tv_id))
                if not step("revoke_verified", res is True, {True: "the tv no longer trusts this key", False: "the tv still trusts this key: revoke did not happen", None: "could not determine (is debugging still on?); not a pass"}[res]):
                    return {"steps": steps, "done": False, "tv": self.view(tv)}
            if not co.get("port_closed"):
                if not debugging_off:
                    step("port_closed", False, "turn usb debugging and network debugging off on the tv (and reboot it), then tick the box")
                    return {"steps": steps, "done": False, "tv": self.view(tv)}
                st = self.probe(tv["ip"], tv["port"])
                if not step("port_closed", st == "closed", {"closed": f"port {tv['port']} is closed", "open": f"port {tv['port']} is still open", "unknown": "could not check; not a pass"}[st]):
                    return {"steps": steps, "done": False, "tv": self.view(tv)}
            if not co.get("key_deleted"):
                self.s.delete_key(tv_id)
                if not step("key_deleted", not self.s.key_exists(tv_id), "this tv's key is deleted"):
                    return {"steps": steps, "done": False, "tv": self.view(tv)}
            tv["adb_open"], tv["paired"] = False, False
            self.s.save(tv)
        return {"steps": steps, "done": True, "tv": self.view(tv)}
