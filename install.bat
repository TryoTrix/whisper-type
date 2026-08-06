@echo off
chcp 65001 >nul 2>&1
title Whisper Dictation Tool - Setup
echo.
echo ============================================
echo   Whisper Dictation Tool - Setup
echo ============================================
echo.

:: Check whether started as admin (keyboard library may need it)
echo [1/6] Checking prerequisites...
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo          Download: https://www.python.org/downloads/
    echo          IMPORTANT: Enable "Add to PATH" during installation!
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   Found Python %%v

python -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip is not available for this Python installation.
    echo.
    pause
    exit /b 1
)
echo   Found pip

:: Check NVIDIA GPU
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARNING] nvidia-smi not found.
    echo           This tool requires an NVIDIA GPU with CUDA support.
    echo           Without a GPU, the model will not be able to load.
    echo.
    choice /m "Continue anyway?"
    if errorlevel 2 exit /b 1
) else (
    echo   Found NVIDIA GPU
)

echo.
echo [2/6] Creating virtual environment...
echo.
if not exist "%~dp0.venv\Scripts\python.exe" (
    python -m venv "%~dp0.venv"
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment at .venv
        echo.
        pause
        exit /b 1
    )
    echo   Created .venv
) else (
    echo   Reusing existing .venv
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
set "VENV_PYTHONW=%~dp0.venv\Scripts\pythonw.exe"

echo.
echo [3/6] Installing Python packages in .venv...
echo.
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo [WARNING] Could not upgrade pip inside .venv.
    echo           Continuing with current pip version.
)

"%VENV_PY%" -m pip install faster-whisper sounddevice keyboard pyperclip pystray Pillow
if errorlevel 1 (
    echo.
    echo [ERROR] Package installation failed.
    echo          Try: "%VENV_PY%" -m pip install --upgrade pip
    echo.
    pause
    exit /b 1
)

echo.
echo [4/6] Creating autostart (Registry Run key)...
echo.

set "SCRIPT_DIR=%~dp0"
:: Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: Set Registry Run key (HKCU, no admin required)
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WhisperDiktiertool /t REG_SZ /d "\"%VENV_PYTHONW%\" \"%SCRIPT_DIR%\whisper-dictate.py\"" /f >nul 2>&1

if not errorlevel 1 (
    echo   Autostart entry created (Registry Run key)
) else (
    echo   [WARNING] Could not create Registry entry.
    echo             Manual fallback: copy whisper-dictate.bat to shell:startup.
)

:: Clean up old .lnk in Startup folder (if present)
set "OLD_LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Whisper Diktiertool.lnk"
if exist "%OLD_LNK%" (
    del "%OLD_LNK%" >nul 2>&1
    echo   Removed old Startup shortcut
)
:: Remove StartupApproved ghost entry
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder" /v "Whisper Diktiertool.lnk" /f >nul 2>&1

echo.
echo [5/6] Downloading Whisper model (large-v3-turbo, ~3 GB)...
echo        This can take a few minutes on first run.
echo.

"%VENV_PY%" -c "from faster_whisper import WhisperModel; print('Loading model...'); m = WhisperModel('large-v3-turbo', device='cuda', compute_type='int8_float16'); print('Model loaded successfully!')"
if errorlevel 1 (
    echo.
    echo [WARNING] Could not load model on GPU.
    echo           Check NVIDIA driver and CUDA installation.
    echo           The model will be retried on first start.
)

echo.
echo [6/6] Starting Whisper Dictation Tool...
echo.
start "" "%VENV_PYTHONW%" "%~dp0whisper-dictate.py"

echo ============================================
echo   Setup complete!
echo ============================================
echo.
echo   Hotkey:    CTRL+ALT+D (start/stop recording)
echo   Tray icon: Gray = loading, Green = ready, Red = recording
echo   Tray icon: Left click = dashboard, right click = dashboard
echo   Autostart: Enabled (starts at Windows login)
echo   Python env: Project-local .venv
echo.
echo   The dictation tool is now running in the system tray.
echo   Wait until the icon turns green, then press CTRL+ALT+D.
echo.
pause
