# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for the ContractGuard desktop backend.

Build from the backend directory:
    uv run pyinstaller --noconfirm --clean pyinstaller.spec

Output: backend/dist/contractguard-backend/contractguard-backend.exe
Electron copies the whole directory into the installer resources.
"""

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

hiddenimports = (
    collect_submodules("uvicorn")
    + collect_submodules("langgraph")
    + collect_submodules("langchain_core")
    + collect_submodules("aiosqlite")
    + collect_submodules("app")
    + collect_submodules("rapidocr")
    + collect_submodules("onnxruntime")
    + collect_submodules("omegaconf")
    + [
        "anyio._backends._asyncio",
        "langgraph.checkpoint.sqlite",
        "langgraph.checkpoint.sqlite.aio",
    ]
)

# jieba 词典、python-docx 模板、pdfminer cmap、RapidOCR 模型与配置都是运行时数据。
datas = (
    collect_data_files("jieba")
    + collect_data_files("docx")
    + collect_data_files("pdfminer")
    + collect_data_files("rapidocr")
)

binaries = collect_dynamic_libs("onnxruntime")

a = Analysis(
    ["desktop_entry.py"],
    pathex=[SPECPATH],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "reportlab", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="contractguard-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="contractguard-backend",
)
