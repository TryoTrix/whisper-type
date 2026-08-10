@echo off
chcp 65001 >nul 2>&1
title Whisper-Type - Setup
echo.
echo ============================================
echo   Whisper-Type - Setup
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
:: Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

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
set "ENABLE_AUTOSTART=0"

"%VENV_PY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] tkinter is not available in this Python installation.
    echo         The tray dashboard and recording overlay require tkinter.
    echo.
    echo         Fix: run your Python installer, choose Modify,
    echo              enable "tcl/tk and IDLE", then rerun install.bat.
    echo.
    echo         If the problem remains after repairing Python, delete .venv
    echo         and rerun install.bat so the environment is recreated.
    echo.
    pause
)
echo   Found tkinter UI support

echo.
echo [3/6] Installing Python packages in .venv...
echo.
"%VENV_PY%" -m pip install --upgrade pip
if errorlevel 1 (
    echo.
    echo [WARNING] Could not upgrade pip inside .venv.
    echo           Continuing with current pip version.
)

"%VENV_PY%" -m pip install faster-whisper sounddevice keyboard pyperclip pystray Pillow nvidia-cublas-cu12
if errorlevel 1 (
    echo.
    echo [ERROR] Package installation failed.
    echo          Try: "%VENV_PY%" -m pip install --upgrade pip
    echo.
    pause
    exit /b 1
)


echo.
echo [4/6] Autostart preference...
choice /c YN /n /m "Enable autostart at Windows login? [Y/N]: "
if errorlevel 2 (
    set "ENABLE_AUTOSTART=0"
) else (
    set "ENABLE_AUTOSTART=1"
)

if "%ENABLE_AUTOSTART%"=="1" (
    echo Creating autostart - Registry Run key...
    echo.

    :: Set Registry Run key (HKCU, no admin required)
    reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WhisperDiktiertool /t REG_SZ /d "\"%VENV_PYTHONW%\" \"%SCRIPT_DIR%\whisper-dictate.py\"" /f >nul 2>&1

    if not errorlevel 1 (
        echo   Autostart entry created
    ) else (
        echo   [WARNING] Could not create Registry entry.
        echo             Manual fallback: add a shortcut to whisper-dictate.bat to shell:startup.
    )
) else (
    echo Skipping autostart setup.
    reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WhisperDiktiertool /f >nul 2>&1
    if not errorlevel 1 (
        echo       Removed existing autostart entry from previous setup.
    )
    echo       To start the dictation manually after login, run whisper-dictate.bat
)

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
echo [6/6] Starting Whisper-Type...
echo.
start "" "%VENV_PYTHONW%" "%~dp0whisper-dictate.py"

echo ============================================
echo   Setup complete!
echo ============================================
echo.
echo   Hotkey:    CTRL+ALT+D (start/stop recording)
echo   Tray icon: Gray = loading, Green = ready, Red = recording
echo   Tray icon: click = dashboard
if "%ENABLE_AUTOSTART%"=="1" (
    echo   Autostart: Enabled (starts at Windows login)
) else (
    echo   Autostart: Disabled
    echo   Start manually with: whisper-dictate.bat
    echo   You can enable autostart later by re-running install.bat
)
echo   Python env: Project-local .venv
echo.
echo   The dictation tool is now running in the system tray.
echo   Wait until the icon turns green, then press CTRL+ALT+D.
echo.

pause
exit /b 0