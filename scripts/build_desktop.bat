@echo off
chcp 65001 >nul
set PYTHONUTF8=1
rem Use mirrors for Electron / NSIS downloads in mainland China.
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
set ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/
cd /d "%~dp0..\desktop"
if exist node_modules goto build
echo Installing desktop dependencies...
call npm install
if errorlevel 1 goto install_failed
:build
call npm run dist
if errorlevel 1 goto build_failed
echo.
echo Installer output: desktop\release
pause
exit /b 0
:build_failed
echo Desktop build failed. See the log above.
pause
exit /b 1
:install_failed
echo Dependency install failed. Check Node.js / npm / network.
pause
exit /b 1
