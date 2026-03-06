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
echo [3/5] Erstelle Autostart (Registry Run-Key)...
echo.

:: pythonw.exe Pfad dynamisch ermitteln
for /f "delims=" %%p in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYTHONW=%%p"
set "SCRIPT_DIR=%~dp0"
:: Trailing backslash entfernen
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: Registry Run-Key setzen (HKCU, kein Admin noetig)
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v WhisperDiktiertool /t REG_SZ /d "\"%PYTHONW%\" \"%SCRIPT_DIR%\whisper-dictate.py\"" /f >nul 2>&1

if not errorlevel 1 (
    echo   Autostart-Eintrag erstellt (Registry Run-Key)
) else (
    echo   [WARNUNG] Registry-Eintrag konnte nicht erstellt werden.
    echo             Manuell: whisper-dictate.bat in shell:startup kopieren.
)

:: Alte .lnk aus Startup-Ordner aufraeumen (falls vorhanden)
set "OLD_LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Whisper Diktiertool.lnk"
if exist "%OLD_LNK%" (
    del "%OLD_LNK%" >nul 2>&1
    echo   Alte Startup-Verknuepfung entfernt
)
:: StartupApproved-Geistereintrag entfernen
reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder" /v "Whisper Diktiertool.lnk" /f >nul 2>&1

echo.
echo [4/5] Lade Whisper-Modell herunter (large-v3-turbo, ca. 3 GB)...
echo        Das kann beim ersten Mal einige Minuten dauern.
echo.

python -c "from faster_whisper import WhisperModel; print('Lade Modell...'); m = WhisperModel('large-v3-turbo', device='cuda', compute_type='int8_float16'); print('Modell erfolgreich geladen!')"
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
echo   Tray-Icon:  Linksklick = Dashboard, Rechtsklick = Dashboard
echo   Autostart: Aktiv (startet bei Windows-Login)
echo.
echo   Das Diktiertool laeuft jetzt im System Tray.
echo   Warte bis das Icon gruen wird, dann CTRL+ALT+D druecken.
echo.
pause
