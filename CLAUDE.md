# AA Claude Programme

> Lokale AI-Tools die auf dem PC laufen. Python 3.12 + NVIDIA RTX 4060 (CUDA).

---

## Projektstruktur

| Datei | Beschreibung |
|-------|-------------|
| `whisper-dictate.py` | Diktiertool: Sprache zu Text via Hotkey, laeuft als Tray-Icon |
| `whisper-dictate.bat` | Starter fuer whisper-dictate (ruft `pythonw` auf, pfadunabhaengig via `%~dp0`) |
| `whisper-restart.bat` | Beendet laufende Instanz und startet neu (kill + wait + start) |
| `whisper-transcribe.py` | Audiodatei zu Text (CLI-Tool, kein Hotkey) |
| `install.bat` | Einrichtung fuer neue PCs: Pakete, Autostart, Modell-Download |
| `whisper-config.json` | Persistente Einstellungen (calm_mode etc.), wird automatisch erstellt |
| `whisper-error.log` | Wird bei CUDA/Modell-Fehlern erstellt (nur wenn Fehler auftritt) |
| `whisper-history.log` | Transkriptions-Log: jede Diktierung mit Timestamp (append, UTF-8) |

---

## Whisper Diktiertool (`whisper-dictate.py`)

### Shortcuts

| Shortcut | Funktion |
|----------|----------|
| `CTRL+ALT+D` | Aufnahme starten/stoppen |
| `CTRL+ALT+W` | Whisper neu starten (kill + start) via Desktop-Verknuepfung |

### Funktionsweise
- **Hotkey:** `CTRL+ALT+D` startet/stoppt die Aufnahme
- **Modell:** `faster-whisper` large-v3, Sprache: Deutsch (beste Qualitaet, auch mit Hintergrundmusik)
- **GPU:** CUDA float16 auf RTX 4060 (~3 GB VRAM)
- **Transkription:** `beam_size=5`, `vad_filter=True`, `condition_on_previous_text=False`, Audio wird als NumPy-Array direkt an Whisper uebergeben (kein WAV-Umweg)
- **INITIAL_PROMPT:** Fachbegriffe die Whisper korrekt erkennen soll (z.B. CLAUDE.md). Konfigurierbar in der `INITIAL_PROMPT` Variable, kein Performance-Impact
- **SPOKEN_PUNCTUATION:** Gesprochene Satzzeichen werden automatisch ersetzt (z.B. "Doppelpunkt" → `:`, "Fragezeichen" → `?`, "Anführungszeichen" → `"`). Konfigurierbar im `SPOKEN_PUNCTUATION` Dictionary
- **Ausgabe:** Transkribierter Text wird via Clipboard in das aktive Fenster eingefuegt
- **Tray-Icon Farben:** Grau = Modell laedt, Gruen = bereit, Rot = Aufnahme laeuft
- **Tray-Tooltip Statistik:** Zeigt heutige Diktate und Audio-Dauer im Tooltip an (z.B. "Heute: 5x, 2.1 Min"). Wird nach jedem Diktat aktualisiert, liest aus `whisper-history.log`
- **Audio-Feedback:** Hoher Beep (800 Hz) bei Start, tiefer Beep (500 Hz) bei Stop (`winsound.Beep`)
- **REC-Overlay:** Roter pulsierender Balken (8px) am oberen Bildschirmrand auf allen Monitoren waehrend der Aufnahme (tkinter, click-through). Mikrofon-Icon (100x100, 8x Supersampling, r_outer=400 fuer lueckenlosen Kreis) mit Electric Border Effect: 90 pre-gerenderte Frames (3s Loop, 30fps) mit echtem 2D Pixel-Displacement (simuliert SVG feDisplacementMap). Dual-Ring-System: innerer Ring (White-hot Core + Sharp + 4 Glow-Layer, border_r=mic_r+1) und aeusserer Orbit-Ring (eigenes Noise-Feld, langsamerer Pan). Fill-Disc (200,42,42, Blur 8) hinter allen Rings fuellt den Bereich zwischen Mic-Icon und Electric Border lueckenlos. Noise-Texturen (5 Oktaven, 520x520) werden zirkulaer gepannt fuer organische Turbulenz. Alle Blur-Layer werden VOR dem Frame-Loop zu 2 Composite-Bildern zusammengefuegt (nur 2 Displacement-Ops pro Frame statt 6, keine Blur-Ops im Loop). Visuelle Effekte: Breathing Pulse (Glow-Intensitaet pulsiert per Sinus), Core-Flash (3 kurze Helligkeits-Blitze pro Loop), Dunkelrot-Compositing (halbtransparente Randpixel → dunkles Rot statt Schwarz). Pre-Rendering laeuft parallel zum Modell-Laden (~5-8s). Fallback: statisches Mic-Icon mit Fill-Disc bis Frames fertig. ~7 MB RAM fuer Frame-Liste
- **History Log:** Jede erfolgreiche Transkription wird mit Timestamp in `whisper-history.log` gespeichert (`[2026-02-17 14:32:05] Text...`)

### Konfiguration (oben im Script)

| Variable | Beschreibung |
|----------|-------------|
| `MODEL_SIZE` | Whisper-Modell (aktuell `large-v3`) |
| `INITIAL_PROMPT` | Fachbegriffe fuer bessere Erkennung (Komma-getrennt) |
| `SPOKEN_PUNCTUATION` | Gesprochene Satzzeichen → echte Zeichen (Regex-Dict) |
| `NO_SPEECH_THRESHOLD` | Deaktiviert (`None`). Whisper's `no_speech_prob` ist bei Deutsch unzuverlaessig (markiert klare Sprache mit 0.97). `vad_filter=True` uebernimmt die Stille-Erkennung |
| `DEBUG_TRANSCRIPTION` | Segment-Details ins History-Log schreiben (True/False) |
| `SHORT_TEXT_MAX_WORDS` | Bei <= N Woertern trailing Punkt entfernen (3) |
| `HALLUCINATION_PHRASES` | Bekannte Whisper-Halluzinationen die gefiltert werden |

### Tray-Menue
- **Calm:** Toggle fuer Calm Mode (Haekchen = aktiv). Ersetzt das animierte Electric Border Overlay durch ein statisches Mic-Icon (weisses Mikrofon in rotem Kreis). Einstellung wird persistent in `whisper-config.json` gespeichert, wirkt sofort ohne Neustart
- **Neustart:** Beendet aktuelle Instanz, wartet 2s (Mutex-Freigabe), startet `pythonw` direkt neu. Verwendet `pythonw -c "import time,subprocess;time.sleep(2);..."` statt `cmd.exe` fuer komplett unsichtbaren Neustart (kein Terminal-Fenster)
- **Beenden:** Beendet das Diktiertool komplett

### Debug-Logging
Bei `DEBUG_TRANSCRIPTION = True` wird jedes Whisper-Segment mit Status ins History-Log geschrieben:
- `KEEP (no_speech=0.12): Text` = Segment wurde uebernommen (no_speech-Wert nur informativ)
- `SKIP (hallucination): Text` = Bekannte Halluzination gefiltert
- Hinweis: `no_speech_prob` wird nur geloggt, nicht zum Filtern verwendet (bei Deutsch unzuverlaessig)

### Trailing Period
Bei kurzen Diktaten (1-3 Woerter) entfernt `remove_trailing_period()` den automatisch von Whisper hinzugefuegten Punkt. Konfigurierbar ueber `SHORT_TEXT_MAX_WORDS`.

### Performance-Metriken
Automatische Eintraege im History-Log:
- `[STARTUP] Modell geladen in 5.2s` = Modell-Ladezeit beim Start
- `[PERF] 12.3s Audio → 8.1s Transkription (1.5x Echtzeit)` = Transkriptions-Performance pro Diktat

### Autostart
Startet automatisch beim Windows-Login via Registry Run-Key:
```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\WhisperDiktiertool
```
- **Wert:** `"C:\...\pythonw.exe" "C:\...\whisper-dictate.py"` (Pfade dynamisch)
- **Kein PowerShell/COM noetig:** Verwendet `winreg` (Python stdlib)
- **Self-Provisioning:** `ensure_autostart()` prueft beim Start ob der Registry-Eintrag korrekt ist und setzt ihn falls nicht (unabhaengig von install.bat)
- **Cleanup:** Alte `.lnk` aus Startup-Ordner und `StartupApproved`-Geistereintrag werden automatisch entfernt

### Manuell starten
```
whisper-dictate.bat
```
Oder direkt: `pythonw whisper-dictate.py`

### Neustart (bei verlorenem Keyboard-Hook)
**Option 1:** Rechtsklick auf Tray-Icon → "Neustart"
**Option 2:** `CTRL+ALT+W` druecken (Desktop-Verknuepfung)
**Option 3:** Manuell:
```
whisper-restart.bat
```
Desktop-Verknuepfung: `Whisper Restart.lnk` auf OneDrive-Desktop (WindowStyle 7, minimiert).
**WICHTIG:** Verknuepfung NICHT vom Desktop entfernen, sonst funktioniert CTRL+ALT+W nicht mehr (Windows Shortcut-Keys sind an .lnk gebunden).

### Single-Instance
Windows Mutex (`WhisperDiktiertool_Mutex`) verhindert Doppelstart. Wenn eine Instanz laeuft, beendet sich eine zweite sofort.

### Architektur
- **Hauptthread:** pystray Tray-Icon (blockierend)
- **Thread 1:** `hotkey_loop` - wartet auf `keyboard.wait(HOTKEY)`, startet erst wenn Modell geladen
- **Thread 2:** `load_model` - laedt Whisper-Modell auf GPU
- **Thread 3:** `RecordingOverlay` - tkinter Fenster, pollt `recording`-Status alle 100ms
- **Thread 4:** `_prerender_frames` - rendert 90 Electric Border Frames beim Start (parallel zu Thread 2+3)

### CUDA DLL-Pfade
Das Script setzt NVIDIA DLL-Pfade manuell fuer cublas und cudnn:
```
Python312/Lib/site-packages/nvidia/cublas/bin
Python312/Lib/site-packages/nvidia/cudnn/bin
```

---

## Modell-Entscheidungen (getestet am 19.02.2026)

| Modell | Ergebnis | Empfehlung |
|--------|----------|------------|
| `large-v3` + float16 + beam_size=5 | Beste Qualitaet, auch mit Hintergrundmusik. Langsamer (~12-16s fuer 5 Saetze) | **Aktuell aktiv** - maximale Qualitaet |
| `large-v3-turbo` + int8_float16 + beam_size=3 | Gute Qualitaet, deutlich schneller (~3-5s). Korrigiert Selbstkorrekturen nicht (z.B. "ich hoffe, ich denke" bleibt stehen) | Schnellere Alternative |
| `distil-large-v3` | Hat Deutsch als Englisch transkribiert, selbst mit `language="de"`. Unbrauchbar fuer Deutsch | Nicht verwenden |
| `TheChola/whisper-large-v3-turbo-german-faster-whisper` | Gated Repo auf HuggingFace, braucht Account + Token. 2.6% WER auf Deutsch. Nicht getestet | Bei Bedarf mit HF-Login testen |

### NPU (Intel Movidius 3700VC im Surface Laptop Studio 2)
- Nicht nutzbar fuer Whisper: OpenVINO hat Movidius-Support nach v2022.3 eingestellt
- Selbst moderne Intel Core Ultra NPUs (10-48 TOPS) sind viel langsamer als RTX 4060 (194 TOPS)
- RTX 4060 mit CUDA bleibt die beste Option

### Neuere Modelle (Stand Februar 2026)
- Kein Whisper v4 veroeffentlicht oder angekuendigt
- OpenAI fokussiert auf Cloud-only Modelle (gpt-4o-transcribe)
- `large-v3-turbo` (Oktober 2024) ist das neueste Open-Source-Modell

---

## Troubleshooting

### CTRL+ALT+D reagiert nicht
1. **Prozess haengt:** Task-Manager oeffnen, `pythonw.exe` beenden, `whisper-dictate.bat` neu starten
2. **Modell nicht geladen:** Tray-Icon pruefen - wenn grau statt gruen, ist das Modell nicht geladen. Pruefen ob `whisper-error.log` existiert
3. **CUDA-Fehler:** `whisper-error.log` im Projektordner lesen. Haeufig: GPU von anderem Prozess belegt, Treiber-Update noetig
4. **Keyboard-Hook verloren:** Nach Sleep/Wake, Windows-Updates oder laengerer Laufzeit (~3h+) kann der Low-Level Keyboard-Hook verloren gehen. `CTRL+ALT+W` zum Neustarten druecken

### Bekannte Eigenheiten
- `pythonw` hat keine Konsole - Fehler sind unsichtbar. Fehler beim Modell-Laden werden in `whisper-error.log` geschrieben
- Der `hotkey_loop` Thread wartet endlos auf `model is not None`. Wenn das Modell nicht laden kann, reagiert der Hotkey nie
- Die `keyboard`-Bibliothek braucht ggf. Admin-Rechte fuer globale Hotkeys (abhaengig von Windows-Version/Einstellungen)
- RAM-Verbrauch von ~228 MB ist normal (~220 MB Basis + ~7 MB Electric Border Frames). Das Modell liegt im GPU VRAM, nicht im System-RAM
- Whisper's `no_speech_prob` ist bei Deutsch unzuverlaessig: klar gesprochene Saetze werden mit 0.97 markiert. Daher ist die Filterung deaktiviert (`NO_SPEECH_THRESHOLD = None`). `vad_filter=True` uebernimmt die Stille-Erkennung auf Audio-Ebene
- Whisper kann Zahlenformate inkonsistent transkribieren (z.B. "140" als "140.000" im Deutschen Tausenderformat). Dies ist eine Modell-Limitation

---

## Performance-Optimierungen (durchgefuehrt)

| Optimierung | Effekt |
|-------------|--------|
| WAV-Umweg eliminiert (direktes NumPy-Array an Whisper) | Schnellere Transkription, weniger I/O |
| `float16` compute_type | Maximale Qualitaet auf RTX 4060 |
| `beam_size=5` | Beste Ergebnisse, etwas langsamer als beam_size=3 |
| `condition_on_previous_text=False` | Weniger Kontext-Overhead |
| Preview-Feature komplett entfernt | Kein GPU-Contention, kein 0-3s Warten auf Preview-Thread-Stop |
| Mikrofon-Icon 8x Supersampling (statt 4x) | Glattere Raender trotz tkinter 1-Bit-Transparenz |
| Rand gegen Dunkelrot composited (statt Schwarz) | Halbtransparente Randpixel werden zu dunklem Rot statt fast-Schwarz |
| Segment-Generator zu Liste (`list(segments)`) | Verhindert Datenverlust bei Iteration-Fehlern |
| Electric Border pre-gerendert (90 Frames) | 0 Rendering-Kosten zur Laufzeit, nur Frame-Index wechseln (<1ms) |
| 2D Pixel-Displacement statt Polyline-Noise | Echtes feDisplacementMap-Ergebnis statt "Wurm"-Effekt |
| Audio-Level Tracking (`audio_level` global) | RMS-Pegel in `audio_callback` berechnet (0.0-1.0), aktuell nicht visuell genutzt |
| Pre-Composite aller Blur-Layer vor Frame-Loop | 2 Displacement-Ops pro Frame statt 6, 0 Blur-Ops im Loop (42s → ~5-8s) |
| Dual-Ring-System (Inner + Outer Orbit) | Aeusserer Ring mit eigenem Noise-Feld, langsamerer Pan, gibt Tiefe |
| White-hot Core + Breathing Pulse + Core-Flash | Plasma-Kern (fast weiss), Glow pulsiert per Sinus, 3 Helligkeits-Blitze pro Loop |
| Dunkelrot-Compositing fuer Electric Border | Halbtransparente Glow-Randpixel → dunkles Rot statt fast-Schwarz |
| Neustart ohne CMD-Fenster | `pythonw -c` statt `cmd.exe /c timeout` fuer komplett unsichtbaren Neustart |
| Fill-Disc hinter Electric Rings | Gefuellter roter Kreis (200,42,42, Blur 8) fuellt Gap zwischen Mic und Ring |
| Mic-Icon r_outer 384→400, border_r +6→+1 | Roter Kreis fuellt Icon komplett, Ring sitzt direkt am Rand |
| no_speech_prob Filterung deaktiviert | Keine verlorenen Segmente mehr (Whisper markierte klare Sprache mit 0.97) |

### Gemessene Performance (22.02.2026)

| Szenario | Audio | Transkription | Echtzeit-Faktor |
|----------|-------|---------------|-----------------|
| Modell laden (Erststart) | - | 5.7s | - |
| Modell laden (Cache) | - | 3.2s | - |
| Kurze Diktate (1-3 Woerter) | 2-4s | 0.5-0.6s | 4-6x |
| Mittlere Diktate (1-2 Saetze) | 4-10s | 0.7-1.2s | 5-10x |
| Langes Diktat (6 Saetze) | 54.6s | 5.0s | 11x |
| Sehr langes Diktat (20 Segmente) | 72.8s | 7.7s | 9.5x |

---

## Python-Abhaengigkeiten

| Paket | Zweck |
|-------|-------|
| `faster-whisper` 1.2.1 | Whisper Speech-to-Text (CTranslate2 Backend) |
| `sounddevice` | Audio-Aufnahme vom Mikrofon |
| `keyboard` | Globaler Hotkey (Low-Level Hook) |
| `pyperclip` | Clipboard-Zugriff fuer Text-Einfuegen |
| `pystray` | System Tray Icon |
| `Pillow` | Icon-Erstellung fuer pystray |
| `numpy` | Audio-Datenverarbeitung |
| `nvidia-cublas-cu*` | CUDA Bibliothek (GPU-Beschleunigung) |
| `nvidia-cudnn-cu*` | CUDA Deep Neural Network Bibliothek |

### Installation (manuell)
```
pip install faster-whisper sounddevice keyboard pyperclip pystray Pillow
```
CUDA/cuDNN werden mit `faster-whisper` automatisch installiert.

### Installation (neuer PC)
Ordner kopieren und `install.bat` ausfuehren. Das Script:
1. Prueft Python, pip und NVIDIA GPU
2. Installiert alle pip-Pakete
3. Erstellt Autostart via Registry Run-Key (HKCU)
4. Laedt das Whisper-Modell herunter (~3 GB fuer large-v3, erster Start)
5. Startet das Diktiertool

Voraussetzung: Python 3.12+ und NVIDIA GPU mit aktuellem Treiber.

---

## Whisper Transkription (`whisper-transcribe.py`)

CLI-Tool fuer laengere Audiodateien:
```
python whisper-transcribe.py "pfad/zur/audiodatei.mp3"
```
- Erstellt `.txt` (Volltext) und `.srt` (Untertitel) neben der Quelldatei
- Unterstuetzte Formate: mp3, wav, m4a, flac, ogg, wma, aac, mp4, mkv, avi
- Gleiche GPU-Einstellungen wie whisper-dictate

---

## Ideen fuer die Zukunft

- **SPOKEN_PUNCTUATION in Config auslagern:** In `whisper-config.json` verschieben statt im Code hardcoded, neue Ersetzungen ohne Script-Edit hinzufuegen
- **whisper-transcribe.py updaten:** Gleiche Settings wie whisper-dictate (vad_filter, Halluzinations-Filter, no_speech deaktiviert)
- **Auto-Reconnect Keyboard-Hook:** Watchdog-Thread der erkennt wenn der Hook nach ~3h/Sleep verloren geht und automatisch neu registriert
- **Sprache umschaltbar:** Per Tray-Menue zwischen Deutsch/Englisch wechseln, oder zweiter Hotkey (z.B. CTRL+ALT+E fuer Englisch)

---

## Systemumgebung

- **Python:** 3.12.0
- **GPU:** NVIDIA GeForce RTX 4060 (8 GB VRAM)
- **NPU:** Intel Movidius 3700VC VPU (nicht nutzbar fuer Whisper)
- **CUDA:** 13.1, Treiber 591.74
- **OS:** Windows 11
- **Geraet:** Surface Laptop Studio 2
- **Modell-Cache:** `~\.cache\huggingface\hub\`
