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
| `install.bat` | Einrichtung fuer neue PCs: .venv, Pakete, Autostart (per Y/N-Abfrage), Modell-Download |
| `uninstall.bat` | Deinstallation (seit PR #1): Registry-Key, optional Logs/Modell-Cache/.venv, alles mit Y/N-Abfrage |
| `whisper-config.json` | ALLE Einstellungen, versioniert (seit PR #1 einzige Config-Quelle, App startet nicht ohne) |
| `whisper-error.log` | Wird bei CUDA/Modell-Fehlern erstellt (nur wenn Fehler auftritt) |
| `whisper-history.log` | Transkriptions-Log: jede Diktierung mit Timestamp (append, UTF-8) |
| `.claude/skills/pr-review/` | Skill `/pr-review N`: rein lesender PR-Sicherheitsscan (`pr-scan.py`) plus Runbook fuer Merge, deutsch-Sync und Neustart (`SKILL.md`), seit 21.08.2026, auf beiden Branches identisch |

---

## Whisper Diktiertool (`whisper-dictate.py`)

### Shortcuts

| Shortcut | Funktion |
|----------|----------|
| `CTRL+ALT+D` | Aufnahme starten/stoppen |
| `CTRL+ALT+W` | Whisper neu starten (kill + start) via Desktop-Verknuepfung |

### Funktionsweise
- **Hotkey:** `CTRL+ALT+D` startet/stoppt die Aufnahme
- **Modell:** `faster-whisper` large-v3-turbo, Sprache: Deutsch (gute Qualitaet, schnell)
- **GPU:** CUDA int8_float16 auf RTX 4060 (~3 GB VRAM)
- **Transkription:** `beam_size=3`, `vad_filter=True`, `condition_on_previous_text=False`, Audio wird als NumPy-Array direkt an Whisper uebergeben (kein WAV-Umweg)
- **Initial Prompt:** Fachbegriffe die Whisper korrekt erkennen soll (z.B. CLAUDE.md, TryoTrix). Seit PR #1 konfigurierbar via `transcription.initial_prompt` in whisper-config.json, kein Performance-Impact
- **SPOKEN_PUNCTUATION:** Gesprochene Satzzeichen werden automatisch ersetzt (z.B. "Doppelpunkt" → `:`, "Fragezeichen" → `?`, "Anführungszeichen" → `"`). Seit PR #1 konfigurierbar via `post_processing.spoken_punctuation` in whisper-config.json. "Punkt" ist bewusst NICHT enthalten (matcht Teilwörter: "Punkte" → ".e"), am 14.08.2026 erneut entfernt nachdem Commit 0d6dbed es wieder eingefuehrt hatte
- **Ausgabe:** Transkribierter Text wird via Clipboard in das aktive Fenster eingefuegt
- **Tray-Icon Farben:** Grau = Modell laedt, Gruen = bereit, Rot = Aufnahme laeuft
- **Tray-Tooltip Statistik:** Zeigt heutige Diktate und Audio-Dauer im Tooltip an (z.B. "Heute: 5x, 2.1 Min"). Wird nach jedem Diktat aktualisiert, liest aus `whisper-history.log`
- **Audio-Feedback:** Hoher Beep (800 Hz) bei Start, tiefer Beep (500 Hz) bei Stop, sanfter Ready-Chime (G5→C6) nach dem Modell-Laden. Seit PR #1 als In-Memory-WAV via `winsound.PlaySound`, Lautstaerke via `audio.beep_volume` (0.0 = stumm bis 1.0)
- **Silence-Auto-Stop:** Aufnahme stoppt automatisch nach anhaltender Stille (`audio.silence_timeout_seconds`, Default 20s, 0 = deaktiviert). Seit PR #1
- **REC-Overlay:** Roter pulsierender Balken (8px) am oberen Bildschirmrand auf allen Monitoren waehrend der Aufnahme (tkinter, click-through). Mikrofon-Icon (100x100, 8x Supersampling, r_outer=400 fuer lueckenlosen Kreis) mit Electric Border Effect: 90 pre-gerenderte Frames (3s Loop, 30fps) mit echtem 2D Pixel-Displacement (simuliert SVG feDisplacementMap). Dual-Ring-System: innerer Ring (White-hot Core + Sharp + 4 Glow-Layer, border_r=mic_r+1) und aeusserer Orbit-Ring (eigenes Noise-Feld, langsamerer Pan). Fill-Disc (200,42,42, Blur 8) hinter allen Rings fuellt den Bereich zwischen Mic-Icon und Electric Border lueckenlos. Noise-Texturen (5 Oktaven, 520x520) werden zirkulaer gepannt fuer organische Turbulenz. Alle Blur-Layer werden VOR dem Frame-Loop zu 2 Composite-Bildern zusammengefuegt (nur 2 Displacement-Ops pro Frame statt 6, keine Blur-Ops im Loop). Visuelle Effekte: Breathing Pulse (Glow-Intensitaet pulsiert per Sinus), Core-Flash (3 kurze Helligkeits-Blitze pro Loop), Dunkelrot-Compositing (halbtransparente Randpixel → dunkles Rot statt Schwarz). Pre-Rendering laeuft parallel zum Modell-Laden (~5-8s). Fallback: statisches Mic-Icon mit Fill-Disc bis Frames fertig. ~7 MB RAM fuer Frame-Liste
- **History Log:** Jede erfolgreiche Transkription wird mit Timestamp in `whisper-history.log` gespeichert (`[2026-02-17 14:32:05] Text...`)

### Konfiguration (`whisper-config.json`, seit PR #1)

Alle Einstellungen liegen in `whisper-config.json` (versioniert, striktes Schema: fehlt ein Pflichtfeld, startet die App nicht und zeigt eine Fehlermeldung). Das Dashboard schreibt die Datei bei UI-Toggles komplett neu (formatiertes JSON).

| Sektion | Wichtige Keys |
|---------|---------------|
| `ui` | `calm_mode` (false), `rec_overlay` (true), `dashboard_history_entries` (8), `preserve_dashboard_history` (true) |
| `logging` | `save_history` (false = Diktattexte nicht loggen, nur Statistik), `max_file_size_mb` (10, Datei wird bei Erreichen GELEERT) |
| `hotkeys` | `dictation` (ctrl+alt+d) |
| `audio` | `sample_rate` (16000), `beep_volume` (0.1), `silence_timeout_seconds` (20, 0 = aus) |
| `model` | `size` (large-v3-turbo), `device` (cuda), `compute_type` (int8_float16), `download_root` (null = Standard-HF-Cache `~\.cache\huggingface\hub`, optionaler Pfad, seit PR #2) |
| `transcription` | `dictation_language` (de), `beam_size` (3), `vad_filter` (true), `initial_prompt`, `no_speech_threshold` (null, bei Deutsch unzuverlaessig!), `short_text_max_words` (3), `debug_transcription` (true) |
| `post_processing` | `apply_spoken_punctuation` (true), `spoken_punctuation`, `word_corrections` (ß→ss, TryoTrix-Fixes), `hallucination_phrases` |

Ueberholt seit PR #1: Die frueheren Script-Konstanten (`MODEL_SIZE`, `INITIAL_PROMPT`, `SPOKEN_PUNCTUATION`, `WORD_CORRECTIONS`, `NO_SPEECH_THRESHOLD`, `DEBUG_TRANSCRIPTION`, `SHORT_TEXT_MAX_WORDS`, `HALLUCINATION_PHRASES`) existieren nicht mehr im Code, alles lebt in der JSON-Config.

### Tray-Icon Interaktion (seit PR #1)
- **Links- UND Rechts-Klick:** Oeffnen/schliessen das Dashboard-Popup (dark-themed, slide-up Animation). Ein natives Kontextmenue gibt es nur noch als Fallback wenn tkinter fehlt. Dashboard zeigt: Status, heutige Statistik (Diktate + Minuten), Verlauf (Anzahl via `ui.dashboard_history_entries`, Klick = kopieren), Aktions-Buttons (REC Overlay, Neustart, Beenden). Schliesst sich automatisch bei Aufnahme-Start
- **REC Overlay Button:** Toggelt `ui.rec_overlay` (roter Balken + Mic-Overlay waehrend Aufnahme an/aus), ersetzt den frueheren Calm-Mode-Button
- **Silence-Stopp Regler:** Zeile unter den Buttons mit [−]/[+] in 5s-Schritten (0 = Aus, max 180s). Schreibt `audio.silence_timeout_seconds` direkt in die Config, gilt ab der naechsten Aufnahme ohne Neustart. Eigenes Feature vom 14.08.2026, auf master (EN) und deutsch
- **Calm Mode:** Nur noch via `ui.calm_mode` in whisper-config.json editierbar (statisches Mic-Icon statt Electric Border). Wirkt ohne Neustart
- **Neustart:** Beendet aktuelle Instanz, wartet 2s (Mutex-Freigabe), startet `pythonw` direkt neu via `pythonw -c "import time,subprocess;time.sleep(2);..."` (kein Terminal-Fenster)
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
- `[STARTUP] Model loaded in 5.2s` = Modell-Ladezeit beim Start (Log-Texte seit PR #1 englisch, auch auf Branch deutsch)
- `[PERF] 12.3s audio -> 8.1s transcription (1.5x real-time)` = Transkriptions-Performance pro Diktat

### Autostart
Startet automatisch beim Windows-Login via Registry Run-Key:
```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\WhisperDiktiertool
```
- **Wert:** `"C:\...\pythonw.exe" "C:\...\whisper-dictate.py"` (Pfade dynamisch)
- **Kein PowerShell/COM noetig:** Verwendet `winreg` (Python stdlib)
- **Seit PR #1 kein Self-Provisioning mehr:** `ensure_autostart()` wurde entfernt, install.bat verwaltet den Autostart (Y/N-Abfrage bei Installation). Der bestehende Registry-Eintrag bleibt gueltig (System-pythonw + Script-Pfad, branch-unabhaengig)
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
- **Hauptthread:** pystray Tray-Icon (blockierend), Links-Klick setzt `_dashboard_toggle` Event
- **Thread 1:** `hotkey_loop` - wartet auf `keyboard.wait(HOTKEY)`, startet erst wenn Modell geladen
- **Thread 2:** `load_model` - laedt Whisper-Modell auf GPU
- **Thread 3:** `RecordingOverlay` - tkinter Fenster, pollt `recording`-Status alle 100ms
- **Thread 4:** `_prerender_frames` - rendert 90 Electric Border Frames beim Start (parallel zu Thread 2+3)

### CUDA DLL-Pfade
Das Script findet die NVIDIA DLLs (cublas, cudnn) seit PR #1 dynamisch via `sysconfig.get_path("purelib")`, funktioniert mit System-Python UND projekt-lokaler .venv:
```
<site-packages>/nvidia/cublas/bin
<site-packages>/nvidia/cudnn/bin
```

---

## Modell-Entscheidungen (getestet am 19.02.2026, gewechselt am 06.03.2026)

| Modell | Ergebnis | Empfehlung |
|--------|----------|------------|
| `large-v3` + float16 + beam_size=5 | Beste Qualitaet, auch mit Hintergrundmusik. Langsamer (~12-16s fuer 5 Saetze) | Maximale Qualitaet, aber zu langsam fuer taeglichen Einsatz |
| `large-v3-turbo` + int8_float16 + beam_size=3 | Gute Qualitaet, deutlich schneller (~3-5s). Transkription und Speed passen beide gut | **Aktuell aktiv** - bester Kompromiss aus Speed und Qualitaet |
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
Ordner kopieren und `install.bat` ausfuehren. Das Script (seit PR #1):
1. Prueft Python, pip, NVIDIA GPU und tkinter
2. Erstellt projekt-lokale `.venv` und installiert alle Pakete dort (inkl. nvidia-cublas-cu12)
3. Fragt per Y/N ob Autostart gewuenscht (Registry Run-Key, HKCU, zeigt dann auf .venv-pythonw)
4. Laedt das Whisper-Modell herunter (~3 GB, erster Start)
5. Startet das Diktiertool. Deinstallation spaeter via `uninstall.bat`

Voraussetzung: Python 3.12+ und NVIDIA GPU mit aktuellem Treiber.

---

## Whisper Transkription (`whisper-transcribe.py`)

CLI-Tool fuer laengere Audiodateien:
```
python whisper-transcribe.py "pfad/zur/audiodatei.mp3" [sprache]
```
- Sprache optional als 2. Argument, Default `de` (nur Branch deutsch; master fragt interaktiv)
- Nutzt automatisch die projekt-lokale `.venv` falls vorhanden (seit PR #1)
- Erstellt `.txt` (Volltext) und `.srt` (Untertitel) neben der Quelldatei
- Unterstuetzte Formate: mp3, wav, m4a, flac, ogg, wma, aac, mp4, mkv, avi
- Gleiche GPU-Einstellungen wie whisper-dictate

---

## GitHub

- **Oeffentliches Repo:** `TryoTrix/whisper-type` (https://github.com/tryotrix/whisper-type)
- **Branches (Stand 21.08.2026):** `master` = englischer Upstream-Stand (PR #1 gemerged als df7f5db, Punkt-Fix 4b2b584, PR #2 gemerged als 91bcec2 + Fix bc63547, Skill 49a6c93, alles gepusht). `deutsch` = aktive Arbeitsversion auf diesem PC (ausgecheckt, gepusht), siehe Abschnitt "Deutsche Version". Die Divergenz vom 04.03.2026 ist seit dem Sync behoben
- **gh CLI:** Nicht installiert. PR-Review seit 21.08.2026 ueber den Skill `/pr-review N` (`.claude/skills/pr-review/`): `pr-scan.py` fetcht den PR-Head nach `pr-N` (nie Checkout, nichts wird ausgefuehrt) und scannt Metadaten, Links, versteckte Unicode-Zeichen, gefaehrliche Code-Muster und Prompt-Injection-Phrasen; `SKILL.md` enthaelt Checkliste, Urteilsregeln und den Ablauf Merge (temporaerer Worktree im Scratchpad) → deutsch-Sync → Neustart → Test. Ohne Go des Users kein Merge, kein Push, kein Kommentar
- **PR #1 (GEMERGED 14.08.2026 als df7f5db):** Externer Contributor "Vousk-prod" (Fork `vousk/whisper-type`), 23 Commits, +1250/-686: EN-Uebersetzung aller Docs/UI/Logs, whisper-config.json als einzige Config-Quelle (versioniert), venv-basierte Installation, uninstall.bat, Silence-Auto-Stop, Log-Rotation, Beep-Lautstaerke, Privacy-Mode. Security-Review vor dem Merge (kompletter Diff + Unicode-/Pattern-Scans): sauber, keine Malware/Exfiltration/Prompt-Injection, Loeschaktionen gezielt + mit Y/N-Abfrage. Der fehlerhafte Punkt-Regex aus der PR-Config wurde direkt nach dem Merge auf master entfernt (4b2b584)
- **PR #2 (GEMERGED 21.08.2026 als 91bcec2):** Externer Contributor tkhyn (Thomas Khyn, Fork `tkhyn/whisper-type`), 1 Zeile: optionales `model.download_root` an `WhisperModel` durchreichen. Security-Review (Scan + Diff + faster-whisper-Quellcode): sauber, keine Links, keine Injection. Bug im Original (`str(None)` = `"None"` als cache_dir → Modell-Neudownload in Ordner `None` fuer alle ohne den Key) im Folge-Commit bc63547 gefixt: Key optional, null/fehlend = Standard-Cache, Pfad wird per expanduser aufgeloest. Config-Key `download_root: null` + README-Zeile ergaenzt

---

## Deutsche Version (Branch `deutsch`)

Der Branch `deutsch` ist die aktive Arbeitsversion auf diesem PC (ausgecheckt; Autostart, CTRL+ALT+W und whisper-restart.bat nutzen automatisch den ausgecheckten Stand). `master` bleibt englischer Upstream fuer Oeffentlichkeit und kuenftige PRs.

**SYNC-REGEL (14.08.2026, vom User festgelegt):** Jede neue Aenderung gehoert auf BEIDE Branches. Ablauf: zuerst auf `master` bauen (englisch/neutral), pushen, dann `git merge master` in `deutsch` und dort nur die Labels eindeutschen. NUR die 4 Deltas unten bleiben deutsch-only. Nach jeder Arbeits-Session pruefen: `git log deutsch..master` muss leer sein (deutsch enthaelt alles von master) und `git diff master deutsch --stat` darf nur CLAUDE.md, whisper-dictate.py, whisper-transcribe.py und whisper-config.json zeigen.

Deltas gegenueber master (Stand 14.08.2026, bewusst deutsch-only):
1. Deutsche CLAUDE.md (diese Datei) statt der englischen Uebersetzung
2. UI-Texte deutsch: Tray-Tooltip (Bereit/Aufnahme/Transkribiere, "Heute: 5x"), Dashboard (Diktate, Min gespart, VERLAUF, Silence-Stopp/Aus, Neustart, Beenden), Fehlermeldungen. Log-Eintraege bleiben englisch (Tags wie [PERF]/[ERROR] werden vom Code geparst)
3. `whisper-transcribe.py`: ohne Sprach-Argument Default `de` (auf master erscheint stattdessen die interaktive Abfrage; das optionale 2. CLI-Argument gibt es auf beiden Branches)
4. whisper-config.json: Schweizer `ß` → `ss` Regel in `post_processing.word_corrections` (z.B. "außerdem" → "ausserdem") + persoenliche Werte (silence_timeout_seconds 0, rec_overlay). Die uebrigen Wortkorrekturen (TryoTrix, CLAUDE.md, faster-whisper) sind auf beiden Branches

README ist seit 14.08.2026 abends auf beiden Branches IDENTISCH (Branch-Hinweis + zweisprachiger Claude-Code-Install-Prompt), bei Merge-Konflikt dort die master-Version nehmen.

Upstream-Updates einspielen:
1. `git checkout master && git pull` danach `git checkout deutsch && git merge master`
2. Konflikte: CLAUDE.md immer unsere Version, README master-Version, whisper-config.json/whisper-transcribe.py unsere Version (enthalten die DE-Deltas), Code-Konflikte in whisper-dictate.py einzeln pruefen (UI-Strings ggf. neu eindeutschen)
3. Neustart (CTRL+ALT+W) + Testdiktat, [PERF]-Zeile im Log gegen Baseline pruefen

---

## Ideen fuer die Zukunft

- ~~SPOKEN_PUNCTUATION in Config auslagern~~ Erledigt durch PR #1 (14.08.2026)
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
