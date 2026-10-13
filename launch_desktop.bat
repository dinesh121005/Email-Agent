@echo off
setlocal
cd /d "%~dp0"

echo =======================================================
echo   Launching Local Mail Agent Desktop Application...
echo =======================================================

uv run python main.py desktop
