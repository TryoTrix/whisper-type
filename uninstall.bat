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
set "REMOVE_SHORTCUT=0"

echo [1/6] Removing autostart registry entries...
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
echo [2/6] Local data preference...
choice /c YN /n /m "Keep local logs/config/history files? [Y/N]: "
if errorlevel 2 (
    set "KEEP_DATA=0"
) else (
    set "KEEP_DATA=1"
)

echo.
echo [3/6] Model cache preference...
choice /c YN /n /m "Keep downloaded Whisper models/cache? [Y/N]: "
if errorlevel 2 (
    set "KEEP_MODELS=0"
) else (
    set "KEEP_MODELS=1"
)

echo.
echo [4/6] Extra cleanup option...
choice /c YN /n /m "Remove desktop shortcut 'Whisper Restart.lnk' if found? [Y/N]: "
if errorlevel 2 (
    set "REMOVE_SHORTCUT=0"
) else (
    set "REMOVE_SHORTCUT=1"
)

echo.
echo [5/6] Removing local environment and caches...
if exist "%SCRIPT_DIR%\.venv" (
    rmdir /s /q "%SCRIPT_DIR%\.venv" >nul 2>&1
    if errorlevel 1 (
        echo   [WARNING] Could not remove .venv (it may be in use).
    ) else (
        echo   Removed .venv
    )
) else (
    echo   No .venv folder found.
)

for /d /r "%SCRIPT_DIR%" %%D in (__pycache__) do (
    rmdir /s /q "%%~fD" >nul 2>&1
)
echo   Removed Python __pycache__ folders (if any).

if "%KEEP_DATA%"=="0" (
    > "%SCRIPT_DIR%\whisper-history.log" (
    )
    > "%SCRIPT_DIR%\whisper-error.log" (
    )
    if exist "%SCRIPT_DIR%\whisper-config.json" del "%SCRIPT_DIR%\whisper-config.json" >nul 2>&1
    echo   Emptied logs and removed local config.
) else (
    echo   Kept logs/config/history files.
)

if "%KEEP_MODELS%"=="0" (
    call :DeleteWhisperModels "%USERPROFILE%\.cache\huggingface\hub"
    call :DeleteWhisperModels "%LOCALAPPDATA%\huggingface\hub"
    echo   Requested model cache cleanup complete.
) else (
    echo   Kept downloaded Whisper models/cache.
)

if "%REMOVE_SHORTCUT%"=="1" (
    call :RemoveRestartShortcut "%USERPROFILE%\Desktop\Whisper Restart.lnk"
    if defined OneDrive call :RemoveRestartShortcut "%OneDrive%\Desktop\Whisper Restart.lnk"
) else (
    echo   Kept desktop restart shortcut.
)

echo.
echo [6/6] Uninstall summary
echo ============================================
echo   Registry autostart entry removed (if present)
echo   Startup leftovers cleaned
echo   Virtual environment removed: .venv
if "%KEEP_DATA%"=="0" (
    echo   Local logs/config: removed or emptied
) else (
    echo   Local logs/config: kept
)
if "%KEEP_MODELS%"=="0" (
    echo   Downloaded models/cache: cleanup attempted
) else (
    echo   Downloaded models/cache: kept
)
if "%REMOVE_SHORTCUT%"=="1" (
    echo   Desktop restart shortcut: cleanup attempted
) else (
    echo   Desktop restart shortcut: kept
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
    "models--Systran--faster-whisper-large-v3*"
    "models--openai--whisper-large-v3*"
    "models--TheChola--whisper-large-v3-turbo-german-faster-whisper*"
    "models--guillaumekln--faster-whisper-*"
) do (
    for /d %%D in ("%HF_HUB%\%%~P") do (
        rmdir /s /q "%%~fD" >nul 2>&1
    )
)
echo   Cleaned Whisper model folders under: %HF_HUB%
goto :eof

:RemoveRestartShortcut
set "SHORTCUT_PATH=%~1"
if exist "%SHORTCUT_PATH%" (
    del "%SHORTCUT_PATH%" >nul 2>&1
    if errorlevel 1 (
        echo   [WARNING] Could not remove: %SHORTCUT_PATH%
    ) else (
        echo   Removed: %SHORTCUT_PATH%
    )
)
goto :eof
