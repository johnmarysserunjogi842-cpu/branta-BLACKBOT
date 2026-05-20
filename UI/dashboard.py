import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import random


# =========================================================
# BLACKBOT PHASE 1 DASHBOARD
# Institutional Tkinter Interface
# =========================================================

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")


class BlackBotDashboard(ctk.CTk):
    def __init__(self):
        super().__init__()

        # -------------------------------------------------
        # WINDOW CONFIG
        # -------------------------------------------------
        self.title("BLACKBOT Terminal")
        self.geometry("1500x900")
        self.minsize(1300, 800)

        self.bg_color = "#0D1117"
        self.panel_color = "#161B22"
        self.border_color = "#30363D"
        self.text_color = "#E6EDF3"
        self.accent = "#DC2626"
        self.green = "#22C55E"
        self.red = "#EF4444"

        self.configure(fg_color=self.bg_color)

        # -------------------------------------------------
        # GRID LAYOUT
        # -------------------------------------------------
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        # -------------------------------------------------
        # BUILD UI
        # -------------------------------------------------
        self.create_sidebar()
        self.create_top_metrics()
        self.create_chart_section()
        self.create_bottom_panels()

    # =====================================================
    # SIDEBAR
    # =====================================================
    def create_sidebar(self):

        self.sidebar = ctk.CTkFrame(
            self,
            width=220,
            fg_color=self.panel_color,
            corner_radius=0,
            border_width=1,
            border_color=self.border_color
        )

        self.sidebar.grid(row=0, column=0, rowspan=3, sticky="nsew")
        self.sidebar.grid_propagate(False)

        # LOGO
        logo = ctk.CTkLabel(
            self.sidebar,
            text="BLACKBOT",
            font=("Segoe UI", 28, "bold"),
            text_color=self.text_color
        )
        logo.pack(pady=(30, 40))

        # NAV BUTTONS
        buttons = [
            "Dashboard",
            "Signals",
            "System Health",
            "Account Info",
            "Market",
            "Analytics",
            "Settings"
        ]

        for btn in buttons:
            button = ctk.CTkButton(
                self.sidebar,
                text=btn,
                height=45,
                corner_radius=10,
                fg_color=self.panel_color,
                hover_color="#21262D",
                border_width=1,
                border_color=self.border_color,
                anchor="w",
                font=("Segoe UI", 14)
            )
            button.pack(fill="x", padx=15, pady=8)

        # START BUTTON
        start_btn = ctk.CTkButton(
            self.sidebar,
            text="▶ START BLACKBOT",
            height=50,
            fg_color=self.accent,
            hover_color="#B91C1C",
            font=("Segoe UI", 15, "bold")
        )
        start_btn.pack(side="bottom", fill="x", padx=15, pady=25)

    # =====================================================
    # TOP METRICS BAR
    # =====================================================
    def create_top_metrics(self):

        self.metrics_frame = ctk.CTkFrame(
            self,
            fg_color=self.bg_color,
            corner_radius=0
        )

        self.metrics_frame.grid(row=0, column=1, sticky="ew", padx=20, pady=20)
        self.metrics_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        metrics = [
            ("BALANCE", "$12,450", self.green),
            ("EQUITY", "$12,980", self.text_color),
            ("TODAY PNL", "+$320", self.green),
            ("DRAWDOWN", "2.1%", self.red)
        ]

        for i, (title, value, color) in enumerate(metrics):

            card = ctk.CTkFrame(
                self.metrics_frame,
                fg_color=self.panel_color,
                border_width=1,
                border_color=self.border_color,
                corner_radius=15,
                height=100
            )

            card.grid(row=0, column=i, sticky="nsew", padx=10)

            title_label = ctk.CTkLabel(
                card,
                text=title,
                font=("Segoe UI", 13),
                text_color="#9CA3AF"
            )
            title_label.pack(pady=(18, 5))

            value_label = ctk.CTkLabel(
                card,
                text=value,
                font=("Segoe UI", 28, "bold"),
                text_color=color
            )
            value_label.pack()

    # =====================================================
    # CHART SECTION
    # =====================================================
    def create_chart_section(self):

        chart_frame = ctk.CTkFrame(
            self,
            fg_color=self.panel_color,
            border_width=1,
            border_color=self.border_color,
            corner_radius=15
        )

        chart_frame.grid(row=1, column=1, sticky="nsew", padx=20)
        chart_frame.grid_columnconfigure(0, weight=1)
        chart_frame.grid_rowconfigure(0, weight=1)

        title = ctk.CTkLabel(
            chart_frame,
            text="Equity Curve",
            font=("Segoe UI", 20, "bold")
        )
        title.pack(anchor="w", padx=20, pady=(15, 0))

        # MATPLOTLIB FIGURE
        fig = Figure(figsize=(10, 4), dpi=100)
        fig.patch.set_facecolor(self.panel_color)

        self.ax = fig.add_subplot(111)
        self.ax.set_facecolor(self.panel_color)

        # SAMPLE DATA
        x = list(range(30))
        y = [10000]

        for _ in range(29):
            y.append(y[-1] + random.randint(-200, 350))

        self.ax.plot(x, y, linewidth=3)

        # CHART STYLE
        self.ax.spines['top'].set_visible(False)
        self.ax.spines['right'].set_visible(False)
        self.ax.spines['left'].set_color('#444')
        self.ax.spines['bottom'].set_color('#444')

        self.ax.tick_params(colors='white')
        self.ax.set_title("BLACKBOT PERFORMANCE", color='white')

        canvas = FigureCanvasTkAgg(fig, master=chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=15, pady=15)

    # =====================================================
    # BOTTOM PANELS
    # =====================================================
    def create_bottom_panels(self):

        bottom_frame = ctk.CTkFrame(
            self,
            fg_color=self.bg_color,
            corner_radius=0
        )

        bottom_frame.grid(row=2, column=1, sticky="nsew", padx=20, pady=20)
        bottom_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # SIGNALS PANEL
        self.create_signals_panel(bottom_frame)

        # MARKET STATUS
        self.create_market_panel(bottom_frame)

        # ALERTS PANEL
        self.create_alerts_panel(bottom_frame)

        # SYSTEM HEALTH
        self.create_health_panel(bottom_frame)

    # =====================================================
    # SIGNALS PANEL
    # =====================================================
    def create_signals_panel(self, parent):

        panel = self.create_panel(parent, "Signals")
        panel.grid(row=0, column=0, sticky="nsew", padx=10)

        signal1 = ctk.CTkTextbox(panel, height=120)
        signal1.pack(fill="x", padx=10, pady=10)

        signal1.insert("0.0",
            "EUR/USD BUY\n\n"
            "Confidence: 87.5%\n"
            "Reason: Trend Reversal\n"
            "Risk Reward: 1:3"
        )

        signal2 = ctk.CTkTextbox(panel, height=120)
        signal2.pack(fill="x", padx=10, pady=10)

        signal2.insert("0.0",
            "GBP/JPY SELL\n\n"
            "Confidence: 72.3%\n"
            "Reason: RSI Overbought\n"
            "Risk Reward: 1:2"
        )

    # =====================================================
    # MARKET PANEL
    # =====================================================
    def create_market_panel(self, parent):

        panel = self.create_panel(parent, "Market Status")
        panel.grid(row=0, column=1, sticky="nsew", padx=10)

        items = [
            "EUR/USD → Bullish",
            "GBP/JPY → Bearish",
            "XAU/USD → Volatile",
            "NASDAQ → Trending"
        ]

        for item in items:
            label = ctk.CTkLabel(
                panel,
                text=item,
                anchor="w",
                font=("Segoe UI", 14)
            )
            label.pack(fill="x", padx=15, pady=10)

    # =====================================================
    # ALERTS PANEL
    # =====================================================
    def create_alerts_panel(self, parent):

        panel = self.create_panel(parent, "Recent Alerts")
        panel.grid(row=0, column=2, sticky="nsew", padx=10)

        alerts = [
            "GBP/JPY position closed",
            "EUR/USD trend reversal",
            "Spread warning on XAU/USD",
            "AI entered safe mode"
        ]

        for alert in alerts:
            label = ctk.CTkLabel(
                panel,
                text=f"• {alert}",
                anchor="w",
                font=("Segoe UI", 13)
            )
            label.pack(fill="x", padx=15, pady=10)

    # =====================================================
    # HEALTH PANEL
    # =====================================================
    def create_health_panel(self, parent):

        panel = self.create_panel(parent, "System Health")
        panel.grid(row=0, column=3, sticky="nsew", padx=10)

        stats = [
            ("Total Trades", "358"),
            ("Win Rate", "62.4%"),
            ("PnL (R)", "+17.32R"),
            ("MT5 Status", "CONNECTED")
        ]

        for title, value in stats:

            container = ctk.CTkFrame(
                panel,
                fg_color="#1E2530",
                corner_radius=10
            )
            container.pack(fill="x", padx=10, pady=8)

            left = ctk.CTkLabel(
                container,
                text=title,
                font=("Segoe UI", 13)
            )
            left.pack(side="left", padx=10, pady=10)

            right = ctk.CTkLabel(
                container,
                text=value,
                font=("Segoe UI", 13, "bold"),
                text_color=self.green
            )
            right.pack(side="right", padx=10)

    # =====================================================
    # PANEL TEMPLATE
    # =====================================================
    def create_panel(self, parent, title_text):

        panel = ctk.CTkFrame(
            parent,
            fg_color=self.panel_color,
            border_width=1,
            border_color=self.border_color,
            corner_radius=15,
            height=260
        )

        title = ctk.CTkLabel(
            panel,
            text=title_text,
            font=("Segoe UI", 18, "bold")
        )
        title.pack(anchor="w", padx=15, pady=(15, 5))

        return panel


# =========================================================
# RUN APPLICATION
# =========================================================
if __name__ == "__main__":
    app = BlackBotDashboard()
    app.mainloop()
