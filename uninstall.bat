@echo off
chcp 65001 >nul 2>&1
title Whisper Dictation Tool - Uninstall
echo.
echo ============================================
echo   Whisper Dictation Tool - Uninstall
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "KEEP_DATA=1"
set "KEEP_MODELS=1"

echo [1/5] Removing autostart registry entries...
reg query "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WhisperDiktiertool >nul 2>&1
if errorlevel 1 (
    echo   Autostart registry key not found.
) else (
    reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WhisperDiktiertool /f >nul 2>&1
    if errorlevel 1 (
        echo   [WARNING] Could not remove autostart registry key.
    ) else (
        echo   Removed autostart registry key.
    )
)

echo.
echo [2/5] Local data preference...
choice /c YN /n /m "Keep local logs/history files? [Y/N]: "
if errorlevel 2 (
    set "KEEP_DATA=0"
) else (
    set "KEEP_DATA=1"
)

if "%KEEP_DATA%"=="0" (
    type nul > "%SCRIPT_DIR%\whisper-history.log"
    type nul > "%SCRIPT_DIR%\whisper-error.log"
    echo   Emptied logs and history.
) else (
    echo   Kept logs/history files.
)

echo.
echo [3/5] Model cache preference...
choice /c YN /n /m "Keep downloaded Whisper models/cache? [Y/N]: "
if errorlevel 2 (
    set "KEEP_MODELS=0"
) else (
    set "KEEP_MODELS=1"
)

if "%KEEP_MODELS%"=="0" (
    if defined HF_HUB_CACHE (
        call :DeleteWhisperModels "%HF_HUB_CACHE%"
    ) else if defined HF_HOME (
        call :DeleteWhisperModels "%HF_HOME%\hub"
    ) else (
        call :DeleteWhisperModels "%USERPROFILE%\.cache\huggingface\hub"
        call :DeleteWhisperModels "%LOCALAPPDATA%\huggingface\hub"
    )
    echo   Requested model cache cleanup complete.
) else (
    echo   Kept downloaded Whisper models/cache.
)

echo.
echo [4/5] Removing local environment and caches...
call :RemoveVenv "%SCRIPT_DIR%\.venv"

for /d /r "%SCRIPT_DIR%" %%D in ("__pycache__") do (
    rmdir /s /q "%%~fD" >nul 2>&1
)
echo   Removed Python __pycache__ folders (if any).

echo.
echo [5/5] Uninstall summary
echo ============================================
echo   Registry autostart entry removed (if present)
echo   Startup leftovers cleaned
echo   Virtual environment cleanup attempted: .venv
if "%KEEP_DATA%"=="0" (
    echo   Local logs/history: emptied
) else (
    echo   Local logs/history: kept
)
if "%KEEP_MODELS%"=="0" (
    echo   Downloaded models/cache: cleanup attempted
) else (
    echo   Downloaded models/cache: kept
)
echo ============================================
echo.
echo If Whisper is still running, close it manually from the system tray or Task Manager.
echo.

pause
exit /b 0

:DeleteWhisperModels
set "HF_HUB=%~1"
if not exist "%HF_HUB%" (
    echo   No Hugging Face hub cache at: %HF_HUB%
    goto :eof
)

for %%P in (
    "models--Systran--faster-whisper-large-v3"
    "models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"
) do (
    if exist "%HF_HUB%\%%~P\" (
        rmdir /s /q "%HF_HUB%\%%~P"
        if exist "%HF_HUB%\%%~P\" (
           echo   [WARNING] Could not remove: %HF_HUB%\%%~P
        ) else (
            echo   Removed: %HF_HUB%\%%~P
        )
    ) else (
        echo   Not found: %HF_HUB%\%%~P
    )
)
echo   Cleaned Whisper model folders under: %HF_HUB%
goto :eof

:RemoveVenv
set "VENV_PATH=%~1"
if not exist "%VENV_PATH%" (
    echo   No .venv folder found.
    goto :eof
)

attrib -r "%VENV_PATH%" /s /d >nul 2>&1

rmdir /s /q "%VENV_PATH%" >nul 2>&1
if errorlevel 1 (
    echo   [WARNING] Could not remove .venv.
    echo   Common causes:
    echo     - a python.exe/pythonw.exe process is still running from this project
    echo     - a terminal/file explorer is open inside .venv
    echo     - antivirus is temporarily locking files
    echo   If a Whipser-Type instance was launched previously, you may need to kill
    echo   python process (or restart the computer) before you can properly uninstall.
    choice /c YN /n /m "   Retry removing .venv now? [Y/N]: "
    if errorlevel 2 (
        echo   Skipped .venv removal.
        goto :eof
    ) else (
        goto :RemoveVenvRetry
    )
) else (
    echo   Removed .venv
    goto :eof
)

:RemoveVenvRetry
rmdir /s /q "%VENV_PATH%" >nul 2>&1
if errorlevel 1 (
    echo   [WARNING] .venv is still locked and was not removed.
) else (
    echo   Removed .venv
)
goto :eof

