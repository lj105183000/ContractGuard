@echo off
chcp 65001 >nul
cd /d "%~dp0..\frontend"
if not exist node_modules (
  call npm install
)
npm run dev
