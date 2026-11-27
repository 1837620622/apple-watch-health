@echo off
chcp 65001 >nul
rem NOTE: no "enabledelayedexpansion" - it silently strips "!" from arguments such as place names.
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
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
  echo ==^> 未检测到 uv，正在通过 PowerShell 自动安装独立版 uv（无需预装 Python）...
  powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
  if exist "%USERPROFILE%\.local\bin\uv.exe" (
    set "UV_BIN=%USERPROFILE%\.local\bin\uv.exe"
  ) else if exist "%USERPROFILE%\.cargo\bin\uv.exe" (
    set "UV_BIN=%USERPROFILE%\.cargo\bin\uv.exe"
  ) else (
    where uv >nul 2>nul
    if %errorlevel%==0 (
      set "UV_BIN=uv"
    ) else (
      echo [ERROR] 自动安装 uv 失败，请手动执行：
      echo   winget install --id=astral-sh.uv -e
      exit /b 1
    )
  )
)

if defined AWH_PYPI_MIRROR (
  if not defined UV_DEFAULT_INDEX set "UV_DEFAULT_INDEX=%AWH_PYPI_MIRROR%"
)

"%UV_BIN%" run --quiet --project "%ROOT%" awh %*
set "AWH_RC=%errorlevel%"
endlocal & exit /b %AWH_RC%
