@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Comanda ai - Agente de Impressao.lnk')); $s.TargetPath=(Join-Path '%~dp0' 'iniciar_agente.bat'); $s.WorkingDirectory='%~dp0'; $s.Save()"
if errorlevel 1 (
  echo Nao foi possivel ativar a inicializacao automatica.
) else (
  echo Agente configurado para iniciar junto com o Windows.
)
pause
