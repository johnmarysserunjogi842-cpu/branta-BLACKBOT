# sidebar.py

import tkinter as tk
from tkinter import ttk


class Sidebar(ttk.Frame):
    """
    Institutional-grade Sidebar (Control Panel)

    Responsibilities:
    - Navigation (view switching)
    - Bot control (start/pause)
    - UI state (active tab, bot state)
    - Thread-safe UI updates

    Guarantees:
    - No direct async calls
    - No backend blocking
    - UI always updated on main thread
    """

    NAV_ITEMS = [
        ("Dashboard", "dashboard"),
        ("Signals", "signals"),
        ("System Health", "system_health"),
        ("Account Info", "account"),
        ("Market", "market"),
    ]

    def __init__(self, parent, controller, on_view_change):
        super().__init__(parent, style="Sidebar.TFrame")

        self.controller = controller
        self.on_view_change = on_view_change

        # UI state
        self.active_key = None
        self.buttons = {}
        self._bot_state = "PAUSED"

        # performance: prevent redundant UI updates
        self._last_rendered_state = None

        self._build_ui()
        self._bind_shortcuts()

    # ==========================================================
    # UI BUILD
    # ==========================================================

    def _build_ui(self):
        self.pack_propagate(False)

        container = ttk.Frame(self, style="Sidebar.TFrame")
        container.pack(fill="both", expand=True, padx=10, pady=10)

        # -----------------------------
        # TITLE
        # -----------------------------
        title = ttk.Label(
            container,
            text="BLACKBOT",
            style="SidebarTitle.TLabel"
        )
        title.pack(pady=(10, 20))

        # -----------------------------
        # NAVIGATION
        # -----------------------------
        for text, key in self.NAV_ITEMS:
            btn = self._create_nav_button(container, text, key)
            btn.pack(fill="x", pady=4)
            self.buttons[key] = btn

        ttk.Separator(container, orient="horizontal").pack(
            fill="x", pady=15
        )

        # -----------------------------
        # BOT CONTROL
        # -----------------------------
        self.bot_button = self._create_control_button(container)
        self.bot_button.pack(fill="x", pady=6)

        # spacer
        ttk.Frame(container).pack(expand=True, fill="both")

        # -----------------------------
        # FOOTER
        # -----------------------------
        footer = ttk.Label(
            container,
            text="software by Branta",
            style="SidebarFooter.TLabel"
        )
        footer.pack(pady=(10, 5))

        # default view
        self._set_active("dashboard")

    # ==========================================================
    # BUTTON FACTORIES
    # ==========================================================

    def _create_nav_button(self, parent, text, key):
        btn = tk.Label(
            parent,
            text=text,
            bg="#111827",
            fg="#9ca3af",
            font=("Segoe UI", 10),
            anchor="w",
            padx=12,
            pady=8,
            cursor="hand2"
        )

        btn.bind("<Enter>", lambda e: self._on_hover(btn, key))
        btn.bind("<Leave>", lambda e: self._restore_style(btn, key))
        btn.bind("<Button-1>", lambda e: self._on_nav_click(key))

        return btn

    def _create_control_button(self, parent):
        btn = tk.Label(
            parent,
            text="▶ Start",
            bg="#1f3a1f",
            fg="#6bff95",
            font=("Segoe UI", 10, "bold"),
            anchor="center",
            padx=12,
            pady=10,
            cursor="hand2"
        )

        btn.bind("<Enter>", lambda e: btn.config(bg="#254d25"))
        btn.bind("<Leave>", lambda e: self._update_bot_button_visual())
        btn.bind("<Button-1>", lambda e: self._on_toggle_bot())

        return btn

    # ==========================================================
    # NAVIGATION
    # ==========================================================

    def _on_nav_click(self, key):
        if key == self.active_key:
            return

        self._set_active(key)
        self.on_view_change(key)

    def _set_active(self, key):
        # reset all
        for k, btn in self.buttons.items():
            btn.config(bg="#111827", fg="#9ca3af")

        # activate selected
        if key in self.buttons:
            btn = self.buttons[key]
            btn.config(bg="#1f2937", fg="#f9fafb")
            self.active_key = key

    def _on_hover(self, btn, key):
        if key != self.active_key:
            btn.config(bg="#1a2233")

    def _restore_style(self, btn, key):
        if key == self.active_key:
            btn.config(bg="#1f2937", fg="#f9fafb")
        else:
            btn.config(bg="#111827", fg="#9ca3af")

    # ==========================================================
    # BOT CONTROL (THREAD SAFE)
    # ==========================================================

    def _on_toggle_bot(self):
        """
        UI thread only.
        Controller handles async internally.
        """
        try:
            self.controller.toggle_bot()
        except Exception:
            return

        # optimistic update
        self._bot_state = (
            "RUNNING" if self._bot_state != "RUNNING" else "PAUSED"
        )
        self._update_bot_button_visual()

    def _update_bot_button_visual(self):
        state = self._bot_state

        if state == self._last_rendered_state:
            return  # avoid redundant redraw

        self._last_rendered_state = state

        if state == "RUNNING":
            self.bot_button.config(
                text="⏸ Pause",
                bg="#3a1f1f",
                fg="#ff6b6b"
            )
        else:
            self.bot_button.config(
                text="▶ Start",
                bg="#1f3a1f",
                fg="#6bff95"
            )

    # ==========================================================
    # EXTERNAL SYNC (SAFE UI UPDATE)
    # ==========================================================

    def sync_bot_state(self, state):
        """
        Called from controller/main loop.
        MUST be thread-safe.
        """
        try:
            root = self.winfo_toplevel()
            root.after(0, lambda: self._apply_state(state))
        except Exception:
            pass

    def _apply_state(self, state):
        if not state:
            return

        self._bot_state = state
        self._update_bot_button_visual()

    # ==========================================================
    # KEYBOARD SHORTCUTS (PRO UX)
    # ==========================================================

    def _bind_shortcuts(self):
        root = self.winfo_toplevel()

        if not root:
            return

        root.bind("<Control-1>", lambda e: self._on_nav_click("dashboard"))
        root.bind("<Control-2>", lambda e: self._on_nav_click("signals"))
        root.bind("<Control-3>", lambda e: self._on_nav_click("system_health"))
        root.bind("<Control-4>", lambda e: self._on_nav_click("account"))
        root.bind("<Control-5>", lambda e: self._on_nav_click("market"))

        root.bind("<space>", lambda e: self._on_toggle_bot())
