@echo off
chcp 65001 >nul
cd /d "%~dp0..\frontend"
if not exist node_modules (
  call npm install
)
call npm run build
echo.
echo 前端构建完成：frontend\dist
echo 启动后端后访问 http://localhost:8000
