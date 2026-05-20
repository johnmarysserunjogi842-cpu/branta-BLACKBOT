# runtime_bootstrap.py

import asyncio
import logging
import os
import sys


# ============================================================
# UTF-8 STABILITY
# ============================================================
os.environ['PYTHONUTF8'] = '1'


# ============================================================
# ASYNCIO POLICY
# ============================================================
if sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(
        asyncio.WindowsSelectorEventLoopPolicy()
    )


# ============================================================
# LOGGING SAFETY
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
