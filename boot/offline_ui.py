#offline_ui.py 
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("OFFLINE_UI")


# ============================================================
# PYINSTALLER SAFE PATHS
# ============================================================

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent


# ============================================================
# UI CONSTANTS
# ============================================================

REFRESH_RATE = 4
MAX_LOG_LINES = 8

STATUS_ONLINE = "[bold green]ONLINE[/bold green]"
STATUS_OFFLINE = "[bold red]OFFLINE[/bold red]"
STATUS_WAITING = "[bold yellow]WAITING[/bold yellow]"
STATUS_UNKNOWN = "[bold magenta]UNKNOWN[/bold magenta]"


# ============================================================
# UI STATE
# ============================================================

@dataclass(slots=True)
class UIState:
    retry_count: int = 0
    started_at: float = time.time()
    last_update: float = time.time()
    status_message: str = "System recovery mode active"


# ============================================================
# OFFLINE UI
# ============================================================

class OfflineUI:
    """
    Institutional-grade recovery dashboard.

    RESPONSIBILITIES:
    - render startup diagnostics
    - display dependency status
    - provide recovery visibility

    NON-RESPONSIBILITIES:
    - runtime supervision
    - startup validation
    - MT5 management
    - runtime orchestration
    """

    def __init__(
        self,
        startup_report,
        base_dir: Optional[Path] = None,
    ) -> None:

        self.startup_report = startup_report

        self.base_dir = base_dir or BASE_DIR

        self.console = Console()

        self.state = UIState()

        self.running = False

        self.logs: list[str] = []

        self._append_log("Offline recovery interface initialized")

    # ========================================================
    # LOGGING
    # ========================================================

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")

        entry = f"[{timestamp}] {message}"

        self.logs.append(entry)

        if len(self.logs) > MAX_LOG_LINES:
            self.logs.pop(0)

    # ========================================================
    # STATUS HELPERS
    # ========================================================

    def _format_status(self, value: bool) -> str:
        if value is True:
            return STATUS_ONLINE

        if value is False:
            return STATUS_OFFLINE

        return STATUS_UNKNOWN

    # ========================================================
    # HEADER
    # ========================================================

    def _build_header(self):
        title = Text(
            "BLACKBOT RECOVERY MODE",
            style="bold cyan",
            justify="center",
        )

        uptime = int(time.time() - self.state.started_at)

        subtitle = Text(
            f"Safe startup recovery layer | uptime={uptime}s",
            style="dim",
            justify="center",
        )

        content = Align.center(
            Text.assemble(title, "\n", subtitle)
        )

        return Panel(
            content,
            border_style="cyan",
            padding=(1, 2),
        )

    # ========================================================
    # SYSTEM STATUS PANEL
    # ========================================================

    def _build_status_panel(self):
        table = Table.grid(expand=True)

        table.add_column(justify="left")
        table.add_column(justify="right")

        table.add_row(
            "Internet",
            self._format_status(
                getattr(self.startup_report, "internet", False)
            ),
        )

        table.add_row(
            "Bridge",
            self._format_status(
                getattr(self.startup_report, "bridge", False)
            ),
        )

        table.add_row(
            "MT5",
            self._format_status(
                getattr(self.startup_report, "mt5", False)
            ),
        )

        table.add_row(
            "Filesystem",
            self._format_status(
                getattr(self.startup_report, "filesystem", False)
            ),
        )

        table.add_row(
            "Environment",
            self._format_status(
                getattr(self.startup_report, "environment", False)
            ),
        )

        table.add_row(
            "Authentication",
            self._format_status(
                getattr(self.startup_report, "auth", False)
            ),
        )

        return Panel(
            table,
            title="[bold white]SYSTEM STATUS[/bold white]",
            border_style="blue",
            padding=(1, 2),
        )

    # ========================================================
    # FAILURE PANEL
    # ========================================================

    def _build_failure_panel(self):
        reason = (
            getattr(
                self.startup_report,
                "failure_reason",
                None,
            )
            or "Unknown startup failure"
        )

        text = Text(
            reason,
            style="bold red",
        )

        return Panel(
            Align.left(text),
            title="[bold white]FAILURE REASON[/bold white]",
            border_style="red",
            padding=(1, 2),
        )

    # ========================================================
    # RECOVERY PANEL
    # ========================================================

    def _build_recovery_panel(self):
        latency = getattr(
            self.startup_report,
            "latency_ms",
            0,
        )

        table = Table.grid(expand=True)

        table.add_column(justify="left")
        table.add_column(justify="right")

        table.add_row(
            "Retry Attempts",
            str(self.state.retry_count),
        )

        table.add_row(
            "Startup Latency",
            f"{latency} ms",
        )

        table.add_row(
            "Recovery Mode",
            "[bold yellow]ACTIVE[/bold yellow]",
        )

        table.add_row(
            "Supervisor State",
            "[bold cyan]WAITING[/bold cyan]",
        )

        return Panel(
            table,
            title="[bold white]RECOVERY STATUS[/bold white]",
            border_style="yellow",
            padding=(1, 2),
        )

    # ========================================================
    # LOG PANEL
    # ========================================================

    def _build_log_panel(self):
        if not self.logs:
            content = Text(
                "No recovery logs available",
                style="dim",
            )

        else:
            content = Text(
                "\n".join(self.logs),
                style="white",
            )

        return Panel(
            content,
            title="[bold white]RECOVERY LOGS[/bold white]",
            border_style="magenta",
            padding=(1, 2),
        )

    # ========================================================
    # FOOTER
    # ========================================================

    def _build_footer(self):
        footer = Text(
            "Awaiting supervisor validation cycle...",
            style="dim cyan",
            justify="center",
        )

        return Panel(
            footer,
            border_style="cyan",
        )

    # ========================================================
    # LAYOUT
    # ========================================================

    def _build_layout(self):
        layout = Layout()

        layout.split_column(
            Layout(name="header", size=5),
            Layout(name="body"),
            Layout(name="footer", size=3),
        )

        layout["body"].split_row(
            Layout(name="left"),
            Layout(name="right"),
        )

        layout["left"].split_column(
            Layout(name="status"),
            Layout(name="recovery"),
        )

        layout["right"].split_column(
            Layout(name="failure"),
            Layout(name="logs"),
        )

        layout["header"].update(self._build_header())

        layout["status"].update(
            self._build_status_panel()
        )

        layout["recovery"].update(
            self._build_recovery_panel()
        )

        layout["failure"].update(
            self._build_failure_panel()
        )

        layout["logs"].update(
            self._build_log_panel()
        )

        layout["footer"].update(
            self._build_footer()
        )

        return layout

    # ========================================================
    # UPDATE REPORT
    # ========================================================

    async def update_report(
        self,
        startup_report,
    ) -> None:
        """
        Safe idempotent report update.
        """

        self.startup_report = startup_report

        self.state.retry_count += 1
        self.state.last_update = time.time()

        self._append_log(
            f"Validation cycle updated | "
            f"ready={startup_report.ready}"
        )

    # ========================================================
    # RENDER LOOP
    # ========================================================

    async def run(self) -> None:
        """
        Lightweight async rendering loop.

        Non-blocking.
        PyInstaller-safe.
        """

        if self.running:
            return

        self.running = True

        self._append_log(
            "Recovery monitoring active"
        )

        try:
            with Live(
                self._build_layout(),
                console=self.console,
                refresh_per_second=REFRESH_RATE,
                screen=True,
                transient=False,
            ) as live:

                while self.running:
                    live.update(
                        self._build_layout()
                    )

                    await asyncio.sleep(
                        1 / REFRESH_RATE
                    )

        except asyncio.CancelledError:
            self._append_log(
                "Recovery interface cancelled"
            )
            raise

        except Exception as exc:
            logger.exception(
                "Offline UI crashed"
            )

            self.console.print(
                f"[bold red]Recovery UI failure:[/bold red] {exc}"
            )

        finally:
            self.running = False

    # ========================================================
    # SAFE STOP
    # ========================================================

    async def stop(self) -> None:
        """
        Idempotent shutdown.
        """

        if not self.running:
            return

        self._append_log(
            "Recovery interface shutting down"
        )

        self.running = False
