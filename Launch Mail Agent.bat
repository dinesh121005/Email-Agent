@echo off
title Local Mail Agent
cd /d "%~dp0"
start "" ".venv\Scripts\pythonw.exe" main.py desktop
exit
