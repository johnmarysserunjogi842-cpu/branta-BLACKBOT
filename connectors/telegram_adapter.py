#connectors/telegram_adapter.py
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict, Any, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.filters import Command

from connectors.app_connector import AppConnector

# ============================================================
# LOGGING
# ============================================================

logger = logging.getLogger("telegram_adapter")
logger.setLevel(logging.INFO)

# ============================================================
# ENV CONFIG (CREDENTIALS ONLY)
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ============================================================
# TELEGRAM ADAPTER
# ============================================================

class TelegramAdapter:
    """
    Telegram transport adapter.

    Responsibilities:
    - Own Telegram credentials
    - Render AppConnector outputs
    - Forward user intent → AppConnector
    - Enforce rate limits
    - Zero trading / business logic
    """

    def __init__(
        self,
        app: AppConnector,
        *,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        self.app = app
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

        self.bot = Bot(
            token=TELEGRAM_BOT_TOKEN,
            parse_mode=ParseMode.HTML,
        )

        self.dp = Dispatcher()

        self._last_send_ts = 0.0
        self._min_send_interval = 1.0  # Telegram-safe

        self._register_routes()
        self._polling_task = None
        self.enabled = bool(
            TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
        )

        if not self.enabled:
            logger.warning(
                'Telegram disabled: missing credentials'
            )
            return
    # ========================================================
    # MESSAGE SENDER (INJECTED INTO AppConnector)
    # ========================================================
    async def start(self):
        if not self.enabled:
            return

        self._polling_task = asyncio.create_task(
            self.dp.start_polling(self.bot)
        )

    async def stop(self):
        if self._polling_task:
            self._polling_task.cancel()

        try:
            await self.bot.session.close()
        except Exception:
            logger.exception('Telegram cleanup failed')

    async def send_message(self, text: str) -> None:
        """
        Async-safe outbound channel.
        Used by AppConnector alert pipeline.
        """
        now = time.time()
        delta = now - self._last_send_ts
        if delta < self._min_send_interval:
            await asyncio.sleep(self._min_send_interval - delta)

        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                disable_web_page_preview=True,
            )
            self._last_send_ts = time.time()
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)

    # ========================================================
    # ROUTES (STRICTLY MAP TO AppConnector)
    # ========================================================

    def _register_routes(self) -> None:
        dp = self.dp

        @dp.message(Command("start"))
        async def _start(msg: types.Message):
            await msg.answer(
                "<b>Trading Bot Online</b>\n\n"
                "/status – Dashboard\n"
                "/signals – Signals\n"
                "/health – System health\n"
                "/account – Account info\n"
                "/market – Market view\n"
                "/toggle – Start / Pause bot"
            )

        @dp.message(Command("status"))
        async def _status(msg: types.Message):
            snap = await self.app.get_dashboard_snapshot()
            await msg.answer(self._fmt_dashboard(snap))

        @dp.message(Command("signals"))
        async def _signals(msg: types.Message):
            data = await self.app.get_signals()
            await msg.answer(self._fmt_signals(data))

        @dp.message(Command("health"))
        async def _health(msg: types.Message):
            data = await self.app.get_system_health()
            await msg.answer(self._fmt_health(data))

        @dp.message(Command("account"))
        async def _account(msg: types.Message):
            data = await self.app.get_account_info()
            await msg.answer(self._fmt_account(data))

        @dp.message(Command("market"))
        async def _market(msg: types.Message):
            data = await self.app.get_market_instruments()
            await msg.answer(self._fmt_market(data))

        @dp.message(Command("toggle"))
        async def _toggle(msg: types.Message):
            await self.app.toggle_bot()
            await msg.answer(f"⏯ Bot state → {self.app.state}")

    # ========================================================
    # FORMATTERS (RENDER ONLY WHAT APP PROVIDES)
    # ========================================================

    def _fmt_dashboard(self, s: Dict[str, Any]) -> str:
        if "error" in s:
            return f"⚠️ {s['error']}"

        lines = [
            "<b>📊 Dashboard</b>",
            f"State: <b>{s['bot_state']}</b>",
            f"Account: {s.get('account_type')}",
            f"Balance: {s['balance']:.2f}",
            f"Equity: {s['equity']:.2f}",
            f"Free Margin: {s['free_margin']:.2f}",
            f"PnL Today: {s['today_pnl']}",
            f"Open Positions: {s.get('total_positions', 0)}",
            "",
            "<b>Sessions</b>",
        ]

        for sym, mode in s.get("session_modes", {}).items():
            lines.append(f"{sym}: {mode}")

        return "\n".join(lines)

    def _fmt_signals(self, d: Dict[str, Any]) -> str:
        rows = d.get("signals", [])
        if not rows:
            return "No active signals"

        lines = ["<b>📡 Signals</b>"]
        for r in rows:
            lines.append(
                f"{r['symbol']} | {r['bias']} | "
                f"conf={r['confidence']:.2f} | "
                f"mode={r['mode']}"
            )
        return "\n".join(lines)

    def _fmt_health(self, h: Dict[str, Any]) -> str:
        return "\n".join(
            [
                "<b>🧠 System Health</b>",
                f"State: {h.get('bot_state')}",
                f"Balance: {h.get('account_balance')}",
                f"Equity: {h.get('equity')}",
                f"Free Margin: {h.get('free_margin')}",
                f"Trades: {h.get('total_trades')}",
                f"Win Rate: {h.get('global_win_rate'):.2%}",
            ]
        )

    def _fmt_account(self, a: Dict[str, Any]) -> str:
        if "error" in a:
            return f"⚠️ {a['error']}"

        return "\n".join(
            [
                "<b>🏦 Account</b>",
                f"Type: {a['account_type']}",
                f"Balance: {a['balance']}",
                f"Equity: {a['equity']}",
                f"Free Margin: {a['free_margin']}",
                f"Leverage: {a['leverage']}",
                f"Currency: {a['currency']}",
            ]
        )

    def _fmt_market(self, m: Dict[str, Any]) -> str:
        lines = ["<b>🌍 Market</b>"]
        for i in m.get("instruments", []):
            lines.append(f"{i['symbol']}: {i['regime']}")
        return "\n".join(lines)

    # ========================================================
    # RUNNER
    # ========================================================

    async def run(self) -> None:
        logger.info("Telegram adapter running")
        await self.dp.start_polling(self.bot)
