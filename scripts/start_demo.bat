@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0.."
if not exist "frontend\dist\index.html" (
  echo 首次运行：安装并构建前端...
  cd frontend
  if not exist node_modules call npm install
  call npm run build
  cd ..
)
cd backend
call uv sync
echo.
echo ContractGuard 单端口演示：http://127.0.0.1:8000
uv run uvicorn app.main:app --port 8000
