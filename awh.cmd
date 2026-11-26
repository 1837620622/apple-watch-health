@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set "ROOT=%~dp0"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

where uv >nul 2>nul
if %errorlevel%==0 (
  set "UV_BIN=uv"
) else if exist "%USERPROFILE%\.local\bin\uv.exe" (
  set "UV_BIN=%USERPROFILE%\.local\bin\uv.exe"
) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
  set "UV_BIN=%USERPROFILE%\.cargo\bin\uv.exe"
) else (
  echo [ERROR] 未找到 uv，正在提示安装命令：
  echo   winget install --id=astral-sh.uv -e
  echo   或 PowerShell: powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
  exit /b 1
)

if defined AWH_PYPI_MIRROR (
  if not defined UV_DEFAULT_INDEX set "UV_DEFAULT_INDEX=%AWH_PYPI_MIRROR%"
)

"%UV_BIN%" run --project "%ROOT%" awh %*
exit /b %errorlevel%
