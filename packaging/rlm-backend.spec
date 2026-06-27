# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: build the RLM backend API into a single executable.

Run from this directory:  pyinstaller rlm-backend.spec --noconfirm
Output: dist/rlm-backend.exe (Windows) / dist/rlm-backend (posix).

The UI (gradio) and MCP server are deliberately excluded — the desktop app
only needs the REST API, and gradio drags in numpy/pandas/matplotlib.
"""

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = []
binaries = []
hiddenimports = []

# Packages with data files / dynamic libs / lazy imports PyInstaller can miss.
# `magic` (python-magic-bin) ships the libmagic DLL + magic database.
for pkg in ("anthropic", "magic", "apscheduler", "pydantic", "email_validator"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# uvicorn resolves its loop/protocol implementations dynamically at runtime.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan.on",
    "anyio._backends._asyncio",
]

# Break import chains we never take so their heavy deps are not bundled.
excludes = [
    "gradio",
    "gradio_client",
    "mcp",
    "matplotlib",
    "tkinter",
    "pytest",
    "IPython",
    "notebook",
    "pandas",
    "scipy",
]

a = Analysis(
    ["rlm_backend.py"],
    pathex=[".."],  # connector root, so `import src...` resolves
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="rlm-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
