@echo off
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\pythonw.exe" (
	start "" "%~dp0.venv\Scripts\pythonw.exe" whisper-dictate.py
) else (
	start "" pythonw whisper-dictate.py
)
