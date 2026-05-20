# /performance/debugger.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple
import time
import os
import json
import threading
import logging
import traceback


# ============================================================
# OPS / DEBUGGER CONTROLLER
# ============================================================
# Responsibilities:
# - Enforce daily maintenance window (00:00-01:00)
# - Hard-block trading via mode="MAINTENANCE" without breaking pipeline
# - Watchdog: detect stalls, repeated failures, degraded states
# - Health & integrity checks (lightweight)
# - Snapshot export + log rotation triggers (optional)
# - Long-term stability: memory pressure / fault streak tracking
#
# Must be:
# - ultra-fast per tick
# - deterministic
# - never throw
# ============================================================


# -----------------------------
# Output contract
# -----------------------------
@dataclass(frozen=True)
class OpsDecision:
    """
    Output of Debugger.tick().

    You apply this decision in your main loop:
    - if decision.force_mode is not None:
        session_ctx.mode = decision.force_mode
    - if decision.request_restart:
        perform controlled restart / reconnect / reload
    """
    now_utc: int
    state: str  # "OK" | "MAINTENANCE" | "DEGRADED" | "HALT"
    force_mode: Optional[str] = None  # e.g. "MAINTENANCE" | "HALT"
    request_restart: bool = False
    reason: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


# -----------------------------
# Internal state model
# -----------------------------
@dataclass
class _RollingStats:
    # lightweight rolling stats (no numpy)
    n: int = 0
    total: float = 0.0
    max_v: float = 0.0

    def add(self, v: float) -> None:
        self.n += 1
        self.total += v
        if v > self.max_v:
            self.max_v = v

    def avg(self) -> float:
        return (self.total / self.n) if self.n > 0 else 0.0


@dataclass
class DebuggerState:
    # time tracking
    last_tick_utc: int = 0
    last_maintenance_day: int = -1

    # fault handling
    fault_streak: int = 0
    last_fault_utc: int = 0
    last_fault_reason: str = ""

    # performance tracking
    tick_latency_ms: _RollingStats = field(default_factory=_RollingStats)
    loop_stall_streak: int = 0

    # maintenance tracking
    in_maintenance: bool = False
    maintenance_enter_utc: int = 0

    # restart policy
    restart_requested_utc: int = 0
    restart_reason: str = ""

    # snapshot export
    last_snapshot_export_utc: int = 0

    # log rotation triggers
    last_log_rotate_utc: int = 0

    # internal lock (not used in hot path unless needed)
    lock: threading.Lock = field(default_factory=threading.Lock)


# ============================================================
# Debugger
# ============================================================

class Debugger:
    """
    Institution-grade Debugger / Ops Controller.

    Usage:
        dbg = Debugger(cache=market_state_cache)
        decision = dbg.tick(orchestrator_result=..., loop_meta=...)
        # apply decision.force_mode / decision.request_restart
    """

    def __init__(
        self,
        *,
        cache,
        logger: Optional[logging.Logger] = None,
        maintenance_start_hhmm: Tuple[int, int] = (0, 0),
        maintenance_duration_seconds: int = 3600,
        max_fault_streak_before_halt: int = 8,
        max_stall_seconds: int = 15,
        snapshot_export_dir: str = "./runtime_snapshots",
        snapshot_export_interval_seconds: int = 300,
        log_rotate_interval_seconds: int = 3600,
        enable_snapshot_export: bool = True,
        enable_log_rotate_hint: bool = True,
        enable_memory_pressure_checks: bool = True,
        hard_halt_on_cache_corruption: bool = False,
    ):
        self.cache = cache
        self.logger = logger or logging.getLogger("Debugger")

        self.maint_hh = int(maintenance_start_hhmm[0])
        self.maint_mm = int(maintenance_start_hhmm[1])
        self.maint_duration = int(max(60, maintenance_duration_seconds))

        self.max_fault_streak_before_halt = int(max(2, max_fault_streak_before_halt))
        self.max_stall_seconds = int(max(2, max_stall_seconds))

        self.snapshot_export_dir = str(snapshot_export_dir)
        self.snapshot_export_interval = int(max(30, snapshot_export_interval_seconds))
        self.log_rotate_interval = int(max(60, log_rotate_interval_seconds))

        self.enable_snapshot_export = bool(enable_snapshot_export)
        self.enable_log_rotate_hint = bool(enable_log_rotate_hint)
        self.enable_memory_pressure_checks = bool(enable_memory_pressure_checks)
        self.hard_halt_on_cache_corruption = bool(hard_halt_on_cache_corruption)

        self.state = DebuggerState()

        # pre-create export dir (non-fatal)
        if self.enable_snapshot_export:
            try:
                os.makedirs(self.snapshot_export_dir, exist_ok=True)
            except Exception:
                pass

    # ========================================================
    # Public API
    # ========================================================

    def tick(
        self,
        *,
        orchestrator_result: Optional[Dict[str, Any]] = None,
        loop_meta: Optional[Dict[str, Any]] = None,
    ) -> OpsDecision:
        """
        Main ops tick. Must be called once per system loop.

        orchestrator_result: output of Orchestrator.run(...)
        loop_meta: optional metadata such as:
            {
              "loop_latency_ms": float,
              "active_symbols": int,
              "thread_workers": int,
              "engine_status": {...}
            }
        """
        t0 = time.perf_counter()
        now_utc = int(time.time())
        meta: Dict[str, Any] = {}

        try:
            # ---------- stall watchdog ----------
            self._stall_watchdog(now_utc)

            # ---------- maintenance policy ----------
            in_maint, maint_meta = self._maintenance_policy(now_utc)
            meta.update(maint_meta)

            # ---------- orchestrator health ----------
            orch_state, orch_meta = self._orchestrator_health(orchestrator_result)
            meta.update(orch_meta)

            # ---------- cache integrity ----------
            cache_ok, cache_meta = self._cache_integrity_check()
            meta.update(cache_meta)

            # ---------- memory pressure ----------
            if self.enable_memory_pressure_checks:
                mem_meta = self._memory_pressure_check()
                meta.update(mem_meta)

            # ---------- optional snapshot export ----------
            if self.enable_snapshot_export:
                self._maybe_export_snapshot(now_utc, orchestrator_result)

            # ---------- optional log rotate hint ----------
            if self.enable_log_rotate_hint:
                self._maybe_rotate_logs_hint(now_utc)

            # ---------- decide ops state ----------
            decision = self._decide(
                now_utc=now_utc,
                in_maintenance=in_maint,
                orch_state=orch_state,
                cache_ok=cache_ok,
                loop_meta=loop_meta,
                meta=meta,
            )

            # ---------- record tick latency ----------
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self.state.tick_latency_ms.add(elapsed_ms)
            decision.meta["debugger_tick_ms"] = round(elapsed_ms, 3)
            decision.meta["debugger_tick_avg_ms"] = round(self.state.tick_latency_ms.avg(), 3)
            decision.meta["debugger_tick_max_ms"] = round(self.state.tick_latency_ms.max_v, 3)

            self.state.last_tick_utc = now_utc
            return decision

        except Exception as e:
            # Debugger must NEVER crash trading system
            self._register_fault(now_utc, f"Debugger.tick exception: {e}")
            self.logger.exception("Debugger.tick failed (non-fatal).")
            return OpsDecision(
                now_utc=now_utc,
                state="DEGRADED",
                force_mode=None,
                request_restart=False,
                reason="Debugger exception (degraded safe mode)",
                meta={
                    "error": str(e),
                    "trace": traceback.format_exc(limit=5),
                },
            )

    # ========================================================
    # Policies / Checks
    # ========================================================

    def _stall_watchdog(self, now_utc: int) -> None:
        last = int(self.state.last_tick_utc or 0)
        if last <= 0:
            return
        delta = now_utc - last
        if delta > self.max_stall_seconds:
            self.state.loop_stall_streak += 1
            self._register_fault(now_utc, f"Loop stall detected: {delta}s since last tick")
        else:
            # decay stall streak
            if self.state.loop_stall_streak > 0:
                self.state.loop_stall_streak -= 1

    def _maintenance_policy(self, now_utc: int) -> Tuple[bool, Dict[str, Any]]:
        """
        Daily maintenance window after midnight:
        - Start: HH:MM (UTC)
        - Duration: 1 hour (default)
        """
        # Determine day boundary
        # Use UTC day number
        day = now_utc // 86400

        # compute today's maintenance start in UTC epoch
        start_today = day * 86400 + self.maint_hh * 3600 + self.maint_mm * 60
        end_today = start_today + self.maint_duration

        in_window = (now_utc >= start_today) and (now_utc < end_today)

        # track transitions
        if in_window and not self.state.in_maintenance:
            self.state.in_maintenance = True
            self.state.maintenance_enter_utc = now_utc
            self.state.last_maintenance_day = int(day)
            self.logger.warning("Entering MAINTENANCE window (daily ops).")

        if (not in_window) and self.state.in_maintenance:
            self.state.in_maintenance = False
            self.logger.warning("Exiting MAINTENANCE window. Resuming normal ops.")

        return in_window, {
            "maintenance": {
                "enabled": True,
                "start_utc": int(start_today),
                "end_utc": int(end_today),
                "in_window": bool(in_window),
                "duration_s": int(self.maint_duration),
            }
        }

    def _orchestrator_health(self, orchestrator_result: Optional[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
        """
        Reads Orchestrator.run(...) output and classifies health.
        """
        if not orchestrator_result or not isinstance(orchestrator_result, dict):
            return "UNKNOWN", {"orchestrator": {"status": "MISSING"}}

        status = str(orchestrator_result.get("status") or "UNKNOWN")
        failed = int(orchestrator_result.get("failed") or 0)
        timed_out = int(orchestrator_result.get("timed_out") or 0)
        completed = int(orchestrator_result.get("completed") or 0)
        submitted = int(orchestrator_result.get("submitted") or 0)
        latency = float(orchestrator_result.get("latency_seconds") or 0.0)

        meta = {
            "orchestrator": {
                "status": status,
                "submitted": submitted,
                "completed": completed,
                "failed": failed,
                "timed_out": timed_out,
                "latency_seconds": round(latency, 6),
            }
        }

        # classify
        if status == "OK":
            return "OK", meta
        if status == "DEGRADED":
            # count as fault but not fatal
            if failed > 0 or timed_out > 0:
                self._register_fault(int(time.time()), f"Orchestrator degraded: failed={failed}, timed_out={timed_out}")
            return "DEGRADED", meta
        if status in ("FAILED", "EMPTY"):
            if status == "FAILED":
                self._register_fault(int(time.time()), "Orchestrator FAILED")
            return status, meta

        return "UNKNOWN", meta

    def _cache_integrity_check(self) -> Tuple[bool, Dict[str, Any]]:
        """
        Very lightweight cache health checks.
        Must not allocate heavy data structures.
        """
        try:
            snap = None
            # snapshot() might be heavy; call only if exists
            if hasattr(self.cache, "snapshot"):
                snap = self.cache.snapshot()

            # minimal checks
            ok = True
            reason = "OK"

            if snap is None:
                return True, {"cache": {"ok": True, "reason": "NO_SNAPSHOT"}}

            if not isinstance(snap, dict):
                ok = False
                reason = f"snapshot_not_dict:{type(snap)}"

            # validate presence of meta/time
            if ok:
                meta = snap.get("meta") if isinstance(snap, dict) else None
                if meta is not None and not isinstance(meta, dict):
                    ok = False
                    reason = "meta_not_dict"

            return ok, {"cache": {"ok": bool(ok), "reason": reason}}

        except Exception as e:
            self._register_fault(int(time.time()), f"Cache integrity check exception: {e}")
            if self.hard_halt_on_cache_corruption:
                return False, {"cache": {"ok": False, "reason": "EXCEPTION_HARD"}}
            return True, {"cache": {"ok": True, "reason": "EXCEPTION_SOFT"}}

    def _memory_pressure_check(self) -> Dict[str, Any]:
        """
        Optional memory pressure check.
        No external deps. Works best on Linux.
        """
        out: Dict[str, Any] = {"memory": {"supported": False}}

        # ru_maxrss exists on unix
        try:
            import resource  # unix only
            rss_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            # ru_maxrss is KB on Linux, bytes on macOS; we don't assume
            out["memory"] = {
                "supported": True,
                "ru_maxrss": rss_kb,
            }
        except Exception:
            pass

        return out

    # ========================================================
    # Export / Ops
    # ========================================================

    def _maybe_export_snapshot(self, now_utc: int, orchestrator_result: Optional[Dict[str, Any]]) -> None:
        """
        Export snapshot periodically for audit/debug.
        Must be safe and non-blocking.
        """
        last = int(self.state.last_snapshot_export_utc or 0)
        if now_utc - last < self.snapshot_export_interval:
            return

        self.state.last_snapshot_export_utc = now_utc

        try:
            snap = None
            if hasattr(self.cache, "snapshot"):
                snap = self.cache.snapshot()

            payload = {
                "ts": now_utc,
                "orchestrator": orchestrator_result or {},
                "cache_snapshot": snap or {},
            }

            # Write atomically
            fn = os.path.join(self.snapshot_export_dir, f"snapshot_{now_utc}.json")
            tmp = fn + ".tmp"

            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)

            os.replace(tmp, fn)

        except Exception as e:
            self._register_fault(now_utc, f"Snapshot export failed: {e}")

    def _maybe_rotate_logs_hint(self, now_utc: int) -> None:
        """
        Hints log rotation by emitting a marker message periodically.
        Real rotation depends on your logging config.
        """
        last = int(self.state.last_log_rotate_utc or 0)
        if now_utc - last < self.log_rotate_interval:
            return
        self.state.last_log_rotate_utc = now_utc
        try:
            self.logger.info("LOG_ROTATE_HINT: periodic rotation marker.")
        except Exception:
            pass

    # ========================================================
    # Decision
    # ========================================================

    def _decide(
        self,
        *,
        now_utc: int,
        in_maintenance: bool,
        orch_state: str,
        cache_ok: bool,
        loop_meta: Optional[Dict[str, Any]],
        meta: Dict[str, Any],
    ) -> OpsDecision:
        """
        Decide final ops state and enforce mode.
        """
        # Hard maintenance window always wins
        if in_maintenance:
            # during maintenance we want NO trades
            return OpsDecision(
                now_utc=now_utc,
                state="MAINTENANCE",
                force_mode="MAINTENANCE",
                request_restart=False,
                reason="Daily maintenance window 00:00-01:00",
                meta=meta,
            )

        # If cache corruption and configured to halt
        if (not cache_ok) and self.hard_halt_on_cache_corruption:
            return OpsDecision(
                now_utc=now_utc,
                state="HALT",
                force_mode="HALT",
                request_restart=True,
                reason="Cache integrity failure (hard halt)",
                meta=meta,
            )

        # Fault streak escalation
        if self.state.fault_streak >= self.max_fault_streak_before_halt:
            # too many consecutive faults -> halt + request restart
            return OpsDecision(
                now_utc=now_utc,
                state="HALT",
                force_mode="HALT",
                request_restart=True,
                reason=f"Fault streak exceeded: {self.state.fault_streak}",
                meta={
                    **meta,
                    "fault_streak": self.state.fault_streak,
                    "last_fault_reason": self.state.last_fault_reason,
                },
            )

        # Degraded orchestrator -> degrade (but do not halt)
        if orch_state in ("DEGRADED", "FAILED"):
            # allow system to run but avoid aggressive trading
            # We do NOT force maintenance; we can optionally force HOLD mode if your session manager supports it.
            # But since your SignalPipeline blocks only MAINTENANCE/OFF/HALT, we keep force_mode None.
            return OpsDecision(
                now_utc=now_utc,
                state="DEGRADED",
                force_mode=None,
                request_restart=False,
                reason=f"Orchestrator health: {orch_state}",
                meta={
                    **meta,
                    "fault_streak": self.state.fault_streak,
                    "loop_meta": loop_meta or {},
                },
            )

        # Loop stall streak can request restart if persistent
        if self.state.loop_stall_streak >= 3:
            # don't halt; request restart
            self.state.restart_requested_utc = now_utc
            self.state.restart_reason = "Persistent loop stalls"
            return OpsDecision(
                now_utc=now_utc,
                state="DEGRADED",
                force_mode=None,
                request_restart=True,
                reason="Persistent loop stalls -> restart requested",
                meta={
                    **meta,
                    "loop_stall_streak": self.state.loop_stall_streak,
                },
            )

        # Normal operation
        # decay fault streak slowly when stable
        if self.state.fault_streak > 0:
            self.state.fault_streak -= 1

        return OpsDecision(
            now_utc=now_utc,
            state="OK",
            force_mode=None,
            request_restart=False,
            reason="OK",
            meta={
                **meta,
                "fault_streak": self.state.fault_streak,
            },
        )

    # ========================================================
    # Fault handling
    # ========================================================

    def _register_fault(self, now_utc: int, reason: str) -> None:
        """
        Records a fault without crashing.
        """
        self.state.fault_streak += 1
        self.state.last_fault_utc = int(now_utc)
        self.state.last_fault_reason = str(reason)[:500]

        try:
            self.logger.warning(f"DEBUGGER_FAULT: streak={self.state.fault_streak} reason={reason}")
        except Exception:
            pass


# ============================================================
# Utility helper (optional)
# ============================================================

def apply_ops_to_session_ctx(session_ctx: Any, decision: OpsDecision) -> Any:
    """
    Optional helper: apply decision.force_mode to your AggressionContext/session ctx.
    Safe: doesn't assume strict types.
    """
    if decision.force_mode:
        try:
            # AggressionContext(mode=...) style
            if hasattr(session_ctx, "mode"):
                session_ctx.mode = decision.force_mode
            elif isinstance(session_ctx, dict):
                session_ctx["mode"] = decision.force_mode
        except Exception:
            pass
    return session_ctx
