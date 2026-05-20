import tkinter as tk
from tkinter import ttk
import logging
import queue
import threading
import time

logger = logging.getLogger(__name__)


class MainWindow:
    """
    =========================================================
    HEDGE FUND TERMINAL v3 (BLOOMBERG STYLE ARCHITECTURE)
    =========================================================

    ✔ Event-driven system
    ✔ Central state cache
    ✔ Modular panels
    ✔ No blocking calls
    ✔ No asyncio in UI thread
    ✔ Scalable to charts + websockets
    =========================================================
    """

    def __init__(self, controller):
        self.controller = controller

        # =========================
        # CORE INFRASTRUCTURE
        # =========================
        self.event_queue = queue.Queue()
        self.state = {}

        # =========================
        # ROOT
        # =========================
        self.root = tk.Tk()
        self.root.title("BLACKBOT Hedge Fund Terminal")
        self.root.geometry("1500x900")
        self.root.minsize(1200, 800)

        self.root.protocol("WM_DELETE_WINDOW", self._close)

        self.running = True

        # =========================
        # BUILD UI
        # =========================
        self._theme()
        self._layout()
        self._header()
        self._workspace()

        # =========================
        # BACKGROUND STATE THREAD
        # =========================
        self.worker = threading.Thread(target=self._state_loop, daemon=True)
        self.worker.start()

        logger.info("🚀 Hedge Fund UI v3 online")

    # =========================================================
    # THEME
    # =========================================================
    def _theme(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        self.bg = "#0a0f1c"
        self.panel = "#111827"
        self.text = "#e5e7eb"

        self.root.configure(bg=self.bg)
        style.configure("TFrame", background=self.bg)
        style.configure("Panel.TFrame", background=self.panel)
        style.configure("TLabel", background=self.bg, foreground=self.text)

    # =========================================================
    # LAYOUT
    # =========================================================
    def _layout(self):
        self.container = ttk.Frame(self.root)
        self.container.pack(fill="both", expand=True)

    # =========================================================
    # HEADER (HEDGE FUND STYLE)
    # =========================================================
    def _header(self):
        self.header = ttk.Frame(self.root, style="Panel.TFrame")
        self.header.pack(fill="x")

        self.status = ttk.Label(self.header, text="STATUS: INIT")
        self.status.pack(side="left", padx=10)

        self.balance = ttk.Label(self.header, text="BAL: --")
        self.balance.pack(side="left", padx=20)

        self.equity = ttk.Label(self.header, text="EQUITY: --")
        self.equity.pack(side="left", padx=20)

        self.pnl = ttk.Label(self.header, text="PnL: --")
        self.pnl.pack(side="left", padx=20)

    # =========================================================
    # WORKSPACE (BLOOMBERG PANELS)
    # =========================================================
    def _workspace(self):
        self.main = ttk.Frame(self.root)
        self.main.pack(fill="both", expand=True)

        # LEFT PANEL (MARKET / SIGNALS)
        self.left = ttk.Frame(self.main, width=300, style="Panel.TFrame")
        self.left.pack(side="left", fill="y")

        ttk.Label(self.left, text="MARKET FEED").pack(pady=5)

        self.signal_box = tk.Text(self.left, height=20, bg="#0f172a", fg="white")
        self.signal_box.pack(fill="both", expand=True)

        # CENTER PANEL (POSITIONS)
        self.center = ttk.Frame(self.main)
        self.center.pack(side="left", fill="both", expand=True)

        self.title = ttk.Label(self.center, text="POSITIONS BOOK", font=("Arial", 16, "bold"))
        self.title.pack(pady=10)

        self.positions = ttk.Label(self.center, text="Positions: --")
        self.positions.pack(pady=5)

        self.alerts = ttk.Label(self.center, text="Alerts: --")
        self.alerts.pack(pady=5)

        # RIGHT PANEL (RISK / LOGS)
        self.right = ttk.Frame(self.main, width=300, style="Panel.TFrame")
        self.right.pack(side="right", fill="y")

        ttk.Label(self.right, text="RISK / LOGS").pack(pady=5)

        self.logs = tk.Text(self.right, bg="#0f172a", fg="white")
        self.logs.pack(fill="both", expand=True)

    # =========================================================
    # EVENT API
    # =========================================================
    def push_event(self, event: dict):
        self.event_queue.put(event)

    # =========================================================
    # BACKGROUND STATE LOOP (SAFE)
    # =========================================================
    def _state_loop(self):
        while self.running:
            try:
                if hasattr(self.controller, "get_dashboard_snapshot"):
                    import asyncio
                    snap = asyncio.run(self.controller.get_dashboard_snapshot())
                    if snap:
                        self.state = snap
                time.sleep(1)
            except Exception as e:
                logger.warning(f"state loop error: {e}")
                time.sleep(2)

    # =========================================================
    # MAIN LOOP
    # =========================================================
    def run(self):
        self._tick()
        self.root.mainloop()

    # =========================================================
    # UI LOOP
    # =========================================================
    def _tick(self):
        try:
            # EVENTS
            while not self.event_queue.empty():
                event = self.event_queue.get()
                self._handle(event)

            # RENDER
            self._render()

        except Exception as e:
            logger.error(f"UI tick error: {e}")

        self.root.after(400, self._tick)

    # =========================================================
    # RENDER ENGINE
    # =========================================================
    def _render(self):
        s = self.state
        if not s:
            return

        self.status.config(text=f"STATUS: {s.get('bot_state')}")
        self.balance.config(text=f"BAL: {s.get('balance')}")
        self.equity.config(text=f"EQUITY: {s.get('equity')}")
        self.pnl.config(text=f"PnL: {s.get('pnl')}")

        self.positions.config(text=f"Positions: {s.get('total_positions')}")
        self.alerts.config(text=f"Alerts: {len(s.get('alerts', []))}")

    # =========================================================
    # EVENT HANDLER
    # =========================================================
    def _handle(self, event):
        msg = str(event)

        self.logs.insert("end", msg + "\n")
        self.logs.see("end")

        self.signal_box.insert("end", msg + "\n")

    # =========================================================
    # CLOSE
    # =========================================================
    def _close(self):
        self.running = False
        try:
            self.root.destroy()
        except Exception:
            pass