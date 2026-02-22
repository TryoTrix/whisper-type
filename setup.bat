@echo off
chcp 65001 >nul 2>&1
title Whisper Diktiertool - Setup
echo.
echo ============================================
echo   Whisper Diktiertool - Setup
echo ============================================
echo.

:: Pruefen ob als Admin gestartet (keyboard-Bibliothek braucht es evtl.)
echo [1/5] Pruefe Voraussetzungen...
echo.

:: Python pruefen
python --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] Python ist nicht installiert oder nicht im PATH.
    echo          Download: https://www.python.org/downloads/
    echo          WICHTIG: Bei der Installation "Add to PATH" aktivieren!
    echo.
    pause
    exit /b 1
)
for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo   Python %%v gefunden

:: pip pruefen
pip --version >nul 2>&1
if errorlevel 1 (
    echo [FEHLER] pip ist nicht verfuegbar.
    echo.
    pause
    exit /b 1
)
echo   pip gefunden

:: NVIDIA GPU pruefen
nvidia-smi >nul 2>&1
if errorlevel 1 (
    echo.
    echo [WARNUNG] nvidia-smi nicht gefunden.
    echo           Das Tool benoetigt eine NVIDIA GPU mit CUDA-Unterstuetzung.
    echo           Ohne GPU wird das Modell nicht laden koennen.
    echo.
    choice /m "Trotzdem fortfahren?"
    if errorlevel 2 exit /b 1
) else (
    echo   NVIDIA GPU gefunden
)

echo.
echo [2/5] Installiere Python-Pakete...
echo.
pip install faster-whisper sounddevice keyboard pyperclip pystray Pillow
if errorlevel 1 (
    echo.
    echo [FEHLER] Paketinstallation fehlgeschlagen.
    echo          Versuche: pip install --upgrade pip
    echo.
    pause
    exit /b 1
)

echo.
echo [3/5] Erstelle Autostart-Verknuepfung...
echo.

:: Startup-Verknuepfung via PowerShell erstellen
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "SCRIPT_DIR=%~dp0"
:: Trailing backslash entfernen
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ws = New-Object -ComObject WScript.Shell; $lnk = $ws.CreateShortcut('%STARTUP%\Whisper Diktiertool.lnk'); $lnk.TargetPath = '%SCRIPT_DIR%\whisper-dictate.bat'; $lnk.WorkingDirectory = '%SCRIPT_DIR%'; $lnk.WindowStyle = 7; $lnk.Description = 'Whisper Diktiertool - Sprache zu Text (CTRL+ALT+D)'; $lnk.Save()"

if exist "%STARTUP%\Whisper Diktiertool.lnk" (
    echo   Autostart-Verknuepfung erstellt
) else (
    echo   [WARNUNG] Verknuepfung konnte nicht erstellt werden.
    echo             Manuell: whisper-dictate.bat in shell:startup kopieren.
)

echo.
echo [4/5] Lade Whisper-Modell herunter (large-v3, ca. 3 GB)...
echo        Das kann beim ersten Mal einige Minuten dauern.
echo.

python -c "from faster_whisper import WhisperModel; print('Lade Modell...'); m = WhisperModel('large-v3', device='cuda', compute_type='float16'); print('Modell erfolgreich geladen!')"
if errorlevel 1 (
    echo.
    echo [WARNUNG] Modell konnte nicht auf GPU geladen werden.
    echo           Pruefe NVIDIA-Treiber und CUDA-Installation.
    echo           Das Modell wird beim ersten Start erneut versucht.
)

echo.
echo [5/5] Starte Whisper Diktiertool...
echo.
start "" pythonw "%~dp0whisper-dictate.py"

echo ============================================
echo   Setup abgeschlossen!
echo ============================================
echo.
echo   Hotkey:    CTRL+ALT+D (Aufnahme starten/stoppen)
echo   Tray-Icon: Grau = laedt, Gruen = bereit, Rot = Aufnahme
echo   Tray-Menue: Rechtsklick fuer Neustart / Beenden
echo   Autostart: Aktiv (startet bei Windows-Login)
echo.
echo   Das Diktiertool laeuft jetzt im System Tray.
echo   Warte bis das Icon gruen wird, dann CTRL+ALT+D druecken.
echo.
pause
