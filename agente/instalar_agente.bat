@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==============================================
echo   COMANDA AI - AGENTE DE IMPRESSAO
echo ==============================================
echo.
where py >nul 2>&1
if errorlevel 1 (
  echo Python nao foi encontrado neste computador.
  echo Instale o Python 3.12 ou superior em python.org e
  echo marque "Add python.exe to PATH" na primeira tela.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -m venv .venv
if errorlevel 1 goto erro
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto erro
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto erro
echo.
echo Instalado. Vamos configurar.
call configurar_agente.bat
exit /b 0
:erro
echo.
echo Nao foi possivel instalar o agente.
echo Confira se este computador tem acesso a internet.
pause
exit /b 1
