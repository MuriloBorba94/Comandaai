@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Execute instalar_agente.bat primeiro.
  pause
  exit /b 1
)
.venv\Scripts\python.exe configurar_agente.py
pause
