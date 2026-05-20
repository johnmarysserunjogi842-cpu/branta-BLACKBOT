# blackbot.spec
# ============================================================
# BLACKBOT Institutional Build Specification
# ============================================================
# Goals:
# - deterministic packaging
# - fast startup
# - runtime parity
# - async stability
# - scalable deployment
# - future plugin compatibility
# - MT5 compatibility
# - robust error handling
# - idempotent builds
# ============================================================

from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.building.build_main import Analysis, PYZ, EXE, COLLECT
from pathlib import Path


# ============================================================
# ROOT PATHS
# ============================================================
ROOT = Path.cwd()


# ============================================================
# DETERMINISTIC HIDDEN IMPORTS
# ============================================================
# NEVER rely on auto-discovery.
# Explicit imports prevent runtime failures.
# ============================================================

hiddenimports = []

# Async / Networking
hiddenimports += collect_submodules('aiohttp')
hiddenimports += collect_submodules('aiogram')
hiddenimports += collect_submodules('asyncio')
hiddenimports += collect_submodules('websockets')
hiddenimports += collect_submodules('tkinter')
hiddenimports += collect_submodules('magic_filter')

# YAML
hiddenimports += collect_submodules('yaml')

# MetaTrader / Trading
try:
    hiddenimports += collect_submodules('MetaTrader5')
except Exception:
    pass

# Numpy / Scientific
hiddenimports += collect_submodules('numpy')




# ============================================================
# RESOURCE PACKAGING
# ============================================================
# Explicit packaging prevents:
# - missing configs
# - runtime crashes
# - EXE/source mismatch
# ============================================================

datas = []
datas += collect_data_files('tkinter')
# Configs
try:
    datas += collect_data_files('configs')
except Exception:
    pass

# YAML
for pattern in [
    'configs/*.yaml',
    'configs/*.yml',
]:
    datas.append((pattern, 'configs'))

# Assets
assets_dir = ROOT / 'assets'
if assets_dir.exists():
    datas.append(('assets', 'assets'))

# UI Resources
ui_dir = ROOT / 'UI'
if ui_dir.exists():
    datas.append(('UI', 'UI'))


# ============================================================
# BINARY PACKAGING
# ============================================================
# Future-proof support:
# - DLLs
# - MT5 binaries
# - external engines
# ============================================================

binaries = []

bin_dir = ROOT / 'binaries'
if bin_dir.exists():
    binaries.append(('binaries', 'binaries'))


# ============================================================
# EXCLUDES
# ============================================================
# Remove garbage modules.
# Reduces:
# - EXE size
# - startup time
# - memory footprint
# ============================================================

excludes = [
    'tkinter.test',
    'matplotlib.tests',
    'numpy.tests',
    'pytest',
    'unittest',
    'IPython',
    'jupyter',
    'notebook',
    'scipy.tests',
]


# ============================================================
# RUNTIME HOOKS
# ============================================================
# Stabilizes:
# - encoding
# - asyncio
# - logging
# - environment
# ============================================================

runtime_hooks = []

hooks_dir = ROOT / 'runtime_hooks'
if hooks_dir.exists():
    runtime_hooks.append(str(hooks_dir / 'runtime_bootstrap.py'))


# ============================================================
# ANALYSIS
# ============================================================

a = Analysis(
    ['bootstrap_blackbot.py'],

    pathex=[str(ROOT)],

    binaries=binaries,

    datas=datas,

    hiddenimports=hiddenimports,

    hookspath=[],

    hooksconfig={},

    runtime_hooks=runtime_hooks,

    excludes=excludes,

    noarchive=False,
)


# ============================================================
# PYZ
# ============================================================

pyz = PYZ(
    a.pure,
    a.zipped_data,
)


# ============================================================
# EXECUTABLE
# ============================================================

exe = EXE(
    pyz,

    a.scripts,

    a.binaries,

    a.zipfiles,

    a.datas,

    [],

    name='BLACKBOT',

    debug=False,

    bootloader_ignore_signals=False,

    strip=False,

    upx=True,

    console=True,

    disable_windowed_traceback=False,

    target_arch=None,

    codesign_identity=None,

    entitlements_file=None,
)


# ============================================================
# COLLECT
# ============================================================
# OneDir = BEST during development.
# Faster startup.
# Easier debugging.
# ============================================================

coll = COLLECT(
    exe,

    a.binaries,

    a.zipfiles,

    a.datas,

    strip=False,

    upx=True,

    upx_exclude=[],

    name='BLACKBOT',
)
