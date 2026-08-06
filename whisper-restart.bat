@echo off
cd /d "%~dp0"
taskkill /F /IM pythonw.exe >nul 2>&1
timeout /t 2 /nobreak >nul
if exist "%~dp0.venv\Scripts\pythonw.exe" (
	start "" "%~dp0.venv\Scripts\pythonw.exe" whisper-dictate.py
) else (
	start "" pythonw whisper-dictate.py
)
