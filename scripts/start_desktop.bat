@echo off
chcp 65001 >nul
set PYTHONUTF8=1
rem Use a mirror for Electron downloads in mainland China.
set ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/
cd /d "%~dp0..\desktop"
if exist node_modules goto run
echo Installing desktop dependencies...
call npm install
if errorlevel 1 goto install_failed
:run
call npm run dev
set EXIT_CODE=%ERRORLEVEL%
echo.
if not "%EXIT_CODE%"=="0" goto failed
echo Desktop exited.
goto hint
:failed
echo Desktop failed with exit code %EXIT_CODE%. See the log above.
:hint
echo If no window appeared, check the system tray for an existing ContractGuard instance.
pause
exit /b %EXIT_CODE%
:install_failed
echo Dependency install failed. Check Node.js / npm / network.
pause
exit /b 1
