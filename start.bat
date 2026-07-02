@echo off
setlocal EnableExtensions EnableDelayedExpansion
title YT Downloader

cd /d "%~dp0"

:: ============================================================
:: Configuration
:: ============================================================

set PYTHON_DIR=python
set PYTHON_EXE=%PYTHON_DIR%\python.exe
set PIP_EXE=%PYTHON_DIR%\Scripts\pip.exe

set FFMPEG_DIR=%PYTHON_DIR%\ffmpeg
set FFMPEG_EXE=%FFMPEG_DIR%\ffmpeg.exe

set REQUIREMENTS=requirements.txt

set PYTHON_VERSION=3.13.7
set PYTHON_ZIP=python-%PYTHON_VERSION%-embed-amd64.zip
set PYTHON_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/%PYTHON_ZIP%

:: FFmpeg is now installed via pip (static-ffmpeg package) instead of
:: downloading the full gyan.dev zip build. This is faster and only
:: pulls the Windows binaries we actually need (ffmpeg + ffprobe).

:: ============================================================
:: Banner
:: ============================================================

cls

echo.
echo =====================================================
echo              YT Downloader Launcher
echo =====================================================
echo.

goto :CHECK_PYTHON
:CHECK_PYTHON

echo [1/7] Checking Embedded Python...

if exist "%PYTHON_EXE%" (
    echo     Found.
    goto :ENABLE_SITE
)

echo     Not Found.
goto :INSTALL_PYTHON
:INSTALL_PYTHON

echo.
echo Downloading Embedded Python...
echo.

curl -L "%PYTHON_URL%" -o "%PYTHON_ZIP%"

if errorlevel 1 (
    echo.
    echo Failed to download Embedded Python.
    pause
    exit /b
)

mkdir "%PYTHON_DIR%"

powershell -NoProfile -Command ^
"Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force"

del "%PYTHON_ZIP%"

echo Embedded Python Installed.

goto :ENABLE_SITE
:ENABLE_SITE

:: Runs every time regardless of whether python.exe already existed —
:: this must never be skipped. Embeddable Python ships with
:: site-packages disabled (pip won't import even if pip.exe physically
:: exists on disk) until this line is uncommented in the ._pth file.
:: The replace is idempotent: if "import site" is already active, this
:: is a harmless no-op.
echo.
echo Enabling site packages...

for %%F in ("%PYTHON_DIR%\python*._pth") do (

    powershell -NoProfile -Command ^
    "(Get-Content '%%F') -replace '#import site','import site' | Set-Content '%%F'"

)

goto :CHECK_PIP
:CHECK_PIP

echo.
echo [2/7] Checking pip...

if exist "%PIP_EXE%" (
    echo     Found.
    goto :UPDATE_PIP
)

echo     Not Found.
goto :INSTALL_PIP
:INSTALL_PIP

echo.
echo Downloading get-pip.py...

curl -L https://bootstrap.pypa.io/get-pip.py -o get-pip.py

if errorlevel 1 (
    echo.
    echo Failed to download get-pip.py
    pause
    exit /b
)

echo.
echo Installing pip...

"%PYTHON_EXE%" get-pip.py

if errorlevel 1 (
    echo.
    echo Failed to install pip.
    pause
    exit /b
)

del get-pip.py

goto :UPDATE_PIP
:UPDATE_PIP

echo.
echo [3/7] Updating pip...

"%PYTHON_EXE%" -m pip install --upgrade pip

goto :INSTALL_REQUIREMENTS
:INSTALL_REQUIREMENTS

echo.
echo [4/7] Installing Python Packages...

if not exist "%REQUIREMENTS%" (

    echo.
    echo requirements.txt not found.
    pause
    exit /b

)

"%PYTHON_EXE%" -m pip install -r "%REQUIREMENTS%"

if errorlevel 1 (

    echo.
    echo Failed to install Python packages.
    pause
    exit /b

)

goto :CHECK_FFMPEG
:CHECK_FFMPEG

echo.
echo [5/7] Checking FFmpeg...

if exist "%FFMPEG_EXE%" (
    echo     Found.
    goto :CHECK_DATABASE
)

echo     Not Found.

goto :INSTALL_FFMPEG
:INSTALL_FFMPEG

echo.
echo Installing FFmpeg via pip (static-ffmpeg)...
echo.

:: static-ffmpeg is already in requirements.txt, so it's installed as
:: part of step [4/7]. This just triggers its one-time binary download
:: (ffmpeg.exe + ffprobe.exe only, no ffplay) and copies the two
:: executables into %FFMPEG_DIR% so the rest of the script/app can
:: keep using %FFMPEG_EXE% exactly as before.

if not exist "%FFMPEG_DIR%" (
    mkdir "%FFMPEG_DIR%"
)

"%PYTHON_EXE%" -c "import shutil; from static_ffmpeg import run; f, p = run.get_or_fetch_platform_executables_else_raise(); shutil.copy(f, r'%FFMPEG_DIR%\ffmpeg.exe'); shutil.copy(p, r'%FFMPEG_DIR%\ffprobe.exe')"

if errorlevel 1 (
    echo.
    echo Failed to install FFmpeg via pip.
    pause
    exit /b
)

if not exist "%FFMPEG_EXE%" (

    echo.
    echo FFmpeg installation failed.
    pause
    exit /b

)

echo.

echo FFmpeg Installed Successfully.

goto :CHECK_DATABASE
:CHECK_DATABASE

echo.
echo [6/7] Checking Database...

if exist "app\database\app.db" (
    echo     Found.
) else (
    echo     Not found - will be created automatically on first launch.
)

goto :LAUNCH_APP
:LAUNCH_APP

echo.
echo [7/7] Starting YT Downloader...
echo.

:: Open the dashboard in the browser after a short delay, from a
:: detached helper — the delay lets the Flask server finish booting
:: before the browser tries to connect.
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5000"

echo =====================================================
echo   YT Downloader is running - dashboard will open in
echo   your browser shortly. Close this window (or press
echo   Ctrl+C) to stop the server.
echo =====================================================
echo.

:: Runs in THIS window (not detached) so closing the window really
:: does stop the server, matching the message above.
"%PYTHON_EXE%" "app\app.py"

pause