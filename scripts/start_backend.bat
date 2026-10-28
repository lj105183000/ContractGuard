@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0..\backend"
uv sync
uv run uvicorn app.main:app --reload --port 8000
