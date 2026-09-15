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
| `whisper-server.py` | Lokaler HTTP-Transkriptionsserver fuers LAN (seit 15.09.2026, fuer das Mappe-Projekt gebaut): `POST /jobs` Upload → `GET /jobs/<id>` Text + Segment- + Wort-Zeitstempel, `POST /transcribe` synchron, CORS, optional `--token`, `--device cpu --compute-type int8 --threads N` simuliert einen CPU-Server. Nur Standardbibliothek, nutzt `whisper-config.json` (Modell, Transkription, Post-Processing) und den zweistufigen Halluzinations-Filter. Audio wird nach dem Dekodieren geloescht. Start: `python whisper-server.py --port 8765`, Windows-Firewall beim ersten Handy-Zugriff freigeben (privates Netz) |
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
- **GPU:** CUDA float16 auf RTX 4060 (~2 GB VRAM). Am 14.09.2026 von int8_float16 umgestellt: float16 ist auf dieser GPU 10-25% schneller, laedt schneller (keine Quantisierung beim Start) und hat keinen Quantisierungsverlust
- **Transkription:** `beam_size=3`, `vad_filter=True`, `condition_on_previous_text=False`, Audio wird als NumPy-Array direkt an Whisper uebergeben (kein WAV-Umweg)
- **Batched Decoding (seit 14.09.2026):** `transcription.batch_size` (Default 8, `0`/`1` = sequenziell) nutzt die `BatchedInferencePipeline` von faster-whisper: die VAD teilt die Aufnahme an Sprechpausen, die Stuecke werden parallel dekodiert. Gemessen auf RTX 4060 mit TTS-Audio: 30 s in 0.8-1.3 s statt 2.0 s, 105 s in 1.9 s statt 7.0 s, Wortfehlerrate gleich oder besser. Braucht `vad_filter=true`, sonst laeuft der sequenzielle Pfad. `[PERF]`-Zeilen enden mit `, batch 8`, wenn der Batched-Pfad lief
- **Warm-up:** `load_model()` transkribiert vor dem Freigeben des Modells zwei Sekunden Stille (batched, 2 Clips, ohne VAD), damit das erste Diktat nach dem Start nicht mehr ~1 s langsamer ist. Log: `[STARTUP] Model loaded in 4.3s (warm-up 1.1s)`
- **Mikrofon-Stream (seit 14.09.2026):** Ein `sd.InputStream` zu erzeugen kostet auf dem MME-Host-API 0.3-0.8 s. Darum wird der Stream vorab erzeugt (`prepare_input_stream()`, beim Start und nach jedem Diktat in einem Hintergrund-Thread) und beim Hotkey nur gestartet (~1 ms, erstes Audio nach ~150 ms). Ein erzeugter, aber gestoppter Stream zaehlt fuer Windows nicht als Mikrofon-Nutzung (per `CapabilityAccessManager`-Zeitstempel verifiziert), das Privacy-Symbol leuchtet nur waehrend der Aufnahme. Weil der Stream nach jedem Diktat neu erzeugt wird, greift auch ein gewechseltes Standard-Mikrofon; schlaegt `start()` fehl, wird einmal ein frischer Stream erzeugt, danach Tray-Meldung «Mikrofon-Fehler»
- **Initial Prompt:** Fachbegriffe die Whisper korrekt erkennen soll (z.B. CLAUDE.md, TryoTrix). Seit PR #1 konfigurierbar via `transcription.initial_prompt` in whisper-config.json, kein Performance-Impact
- **SPOKEN_PUNCTUATION:** Gesprochene Satzzeichen werden automatisch ersetzt (z.B. "Doppelpunkt" → `:`, "Fragezeichen" → `?`, "Anführungszeichen" → `"`). Seit PR #1 konfigurierbar via `post_processing.spoken_punctuation` in whisper-config.json. "Punkt" ist bewusst NICHT enthalten (matcht Teilwörter: "Punkte" → ".e"), am 14.08.2026 erneut entfernt nachdem Commit 0d6dbed es wieder eingefuehrt hatte
- **Ausgabe:** Transkribierter Text wird via Clipboard in das aktive Fenster eingefuegt (`paste_text()`). Der vorherige Clipboard-Inhalt kommt erst nach `ui.clipboard_restore_delay_seconds` (Default 3 s) zurueck und nur, wenn niemand sonst das Clipboard geaendert hat (`GetClipboardSequenceNumber`). Bis 15.09.2026 stand hier fest 150 ms: ein beschaeftigtes Zielfenster (Terminal, das gerade Output rendert) las das Clipboard erst danach und fuegte die ALTE Nachricht ein. Vor Ctrl+V wartet das Tool bis 0.5 s, bis Ctrl/Alt vom Hotkey losgelassen sind (sonst kommt Ctrl+Alt+V an)
- **Tray-Icon Farben:** Grau = Modell laedt, Gruen = bereit, Rot = Aufnahme laeuft
- **Tray-Tooltip Statistik:** Zeigt heutige Diktate und Audio-Dauer im Tooltip an (z.B. "Heute: 5x, 2.1 Min"). Wird nach jedem Diktat aktualisiert, liest aus `whisper-history.log`
- **Audio-Feedback:** Hoher Beep (800 Hz) bei Start, tiefer Beep (500 Hz) bei Stop, sanfter Ready-Chime (G5→C6) nach dem Modell-Laden. Seit 14.09.2026 werden die Toene einmal in Temp-WAVs gerendert (`%TEMP%\whisper-type-*.wav`) und mit `winsound.PlaySound(SND_FILENAME | SND_ASYNC)` abgespielt, also ohne zu blockieren: die In-Memory-Variante aus PR #1 (`SND_MEMORY`) kann nicht asynchron spielen und blockierte den Hotkey-Thread ~300 ms pro Beep (gemessen; `winsound.Beep` vor PR #1: ~105 ms). Audio waehrend des Start-Beeps (`BEEP_DURATION_MS` + 30 ms) wird in `audio_callback` verworfen, der Beep landet nie in der Transkription. Lautstaerke via `audio.beep_volume` (0.0 = stumm bis 1.0); Fallback auf blockierendes In-Memory-Abspielen, falls die Temp-Datei nicht schreibbar ist
- **Silence-Auto-Stop:** Aufnahme stoppt automatisch nach anhaltender Stille (`audio.silence_timeout_seconds`, Default 20s, 0 = deaktiviert). Seit PR #1
- **REC-Overlay:** Roter pulsierender Balken (8px) am oberen Bildschirmrand auf allen Monitoren waehrend der Aufnahme (tkinter, click-through). Mikrofon-Icon (100x100, 8x Supersampling, r_outer=400 fuer lueckenlosen Kreis) mit Electric Border Effect: 90 pre-gerenderte Frames (3s Loop, 30fps) mit echtem 2D Pixel-Displacement (simuliert SVG feDisplacementMap). Dual-Ring-System: innerer Ring (White-hot Core + Sharp + 4 Glow-Layer, border_r=mic_r+1) und aeusserer Orbit-Ring (eigenes Noise-Feld, langsamerer Pan). Fill-Disc (200,42,42, Blur 8) hinter allen Rings fuellt den Bereich zwischen Mic-Icon und Electric Border lueckenlos. Noise-Texturen (5 Oktaven, 520x520) werden zirkulaer gepannt fuer organische Turbulenz. Alle Blur-Layer werden VOR dem Frame-Loop zu 2 Composite-Bildern zusammengefuegt (nur 2 Displacement-Ops pro Frame statt 6, keine Blur-Ops im Loop). Visuelle Effekte: Breathing Pulse (Glow-Intensitaet pulsiert per Sinus), Core-Flash (3 kurze Helligkeits-Blitze pro Loop), Dunkelrot-Compositing (halbtransparente Randpixel → dunkles Rot statt Schwarz). Pre-Rendering laeuft parallel zum Modell-Laden (~5-8s). Fallback: statisches Mic-Icon mit Fill-Disc bis Frames fertig. ~7 MB RAM fuer Frame-Liste
- **History Log:** Jede erfolgreiche Transkription wird mit Timestamp in `whisper-history.log` gespeichert (`[2026-02-17 14:32:05] Text...`)

### Konfiguration (`whisper-config.json`, seit PR #1)

Alle Einstellungen liegen in `whisper-config.json` (versioniert, striktes Schema: fehlt ein Pflichtfeld, startet die App nicht und zeigt eine Fehlermeldung). Das Dashboard schreibt die Datei bei UI-Toggles komplett neu (formatiertes JSON).

| Sektion | Wichtige Keys |
|---------|---------------|
| `ui` | `calm_mode` (false), `rec_overlay` (true), `dashboard_history_entries` (8), `preserve_dashboard_history` (true), `clipboard_restore_delay_seconds` (3.0, optional, seit 15.09.2026, 0 = Diktat bleibt im Clipboard) |
| `logging` | `save_history` (false = Diktattexte nicht loggen, nur Statistik), `max_file_size_mb` (10, Datei wird bei Erreichen GELEERT) |
| `hotkeys` | `dictation` (ctrl+alt+d) |
| `audio` | `sample_rate` (16000), `beep_volume` (0.1), `silence_timeout_seconds` (20, 0 = aus) |
| `model` | `size` (large-v3-turbo), `device` (cuda), `compute_type` (float16, bis 14.09.2026 int8_float16), `download_root` (null = Standard-HF-Cache `~\.cache\huggingface\hub`, optionaler Pfad, seit PR #2) |
| `transcription` | `dictation_language` (de), `beam_size` (3), `batch_size` (8, optional, seit 14.09.2026, 0/1 = sequenziell), `vad_filter` (true), `initial_prompt`, `no_speech_threshold` (null, bei Deutsch unzuverlaessig!), `short_text_max_words` (3), `debug_transcription` (true) |
| `post_processing` | `apply_spoken_punctuation` (true), `spoken_punctuation`, `word_corrections` (ß→ss, TryoTrix-Fixes), `hallucination_phrases` (immer verworfen), optional seit 14.09.2026: `hallucination_patterns` (Regex, immer verworfen, faengt «Untertitelung des ZDF, 2020» / «Untertitelung. BR 2018»), `hallucination_phrases_low_confidence` + `hallucination_logprob_threshold` (-1.0): Alltagsphrasen wie «vielen dank» fliegen nur raus, wenn `avg_logprob` des Segments unter dem Schwellwert liegt. Vorher wurde ein echtes «Vielen Dank.» am Diktat-Ende jedes Mal geloescht (12 Faelle im Log) |

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
- `KEEP (no_speech=0.12, logprob=-0.30): Text` = Segment wurde uebernommen (no_speech-Wert nur informativ, logprob = Whisper-Konfidenz)
- `SKIP (hallucination, logprob=-0.90): Text` = Bekannte Halluzination gefiltert (Phrasenliste oder Regex)
- `SKIP (low-confidence phrase, logprob=-1.60): Text` = Alltagsphrase (z.B. «Vielen Dank.») verworfen, weil Whisper unsicher war
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
| `large-v3-turbo` + int8_float16 + beam_size=3 | Gute Qualitaet, deutlich schneller (~3-5s). Transkription und Speed passen beide gut | Aktiv vom 06.03. bis 14.09.2026 |
| `large-v3-turbo` + float16 + beam_size=3 + batch_size=8 | Gleiche Qualitaet wie int8_float16 auf TTS-Testaudio (WER 0-2%), 10-25% schneller pro Chunk, plus 2-3x auf langen Diktaten durch Batched Decoding (14.09.2026) | **Aktuell aktiv** |
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

### Alte Nachricht statt neuem Diktat eingefuegt (15.09.2026 behoben)
Symptom: Transkription im Log korrekt, im Fenster erscheint aber das vorherige Diktat. Ursache: der alte Clipboard-Inhalt wurde fest 150 ms nach Ctrl+V wiederhergestellt; ein beschaeftigtes Zielfenster (Claude Code in Windows Terminal, waehrend eine Antwort streamt) verarbeitete das Ctrl+V erst spaeter und fuegte den bereits zurueckgestellten alten Inhalt ein. Nachweis: Windows-Clipboard-History (Win+V) per WinRT auslesen, Script `.planning/whisper-paste-2026-09-15/clip-history.ps1` (lokal). Fix: `paste_text()` stellt erst nach `ui.clipboard_restore_delay_seconds` (3 s) wieder her und nur bei unveraenderter Sequence-Number. Im Log zeigt `[DEBUG] Clipboard restored after 3.0s` bzw. `... restore skipped: clipboard changed by another app` den Ausgang, `[DEBUG] Paste waited ...` meldet noch gehaltene Modifier-Tasten.

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
| Asynchrone Beeps aus Temp-WAVs (14.09.2026) | Hotkey-Thread blockiert nicht mehr ~300 ms pro Beep (Regression aus PR #1); Stop-Beep verzoegert die Transkription nicht mehr |
| Vorbereiteter Mikrofon-Stream (14.09.2026) | Aufnahme laeuft ~150 ms nach dem Hotkey statt nach 0.6-1.1 s; weniger abgeschnittene erste Woerter |
| Modell-Warm-up beim Start (14.09.2026) | Erstes Diktat pro Session nicht mehr ~1 s langsamer (Log vorher: Median 5.6x statt 13.5x Echtzeit beim ersten Diktat) |
| `BatchedInferencePipeline` + float16 (14.09.2026) | 30 s Audio 2.0 s → 0.8-1.3 s, 105 s Audio 7.0 s → 1.9 s, gleiche WER |

### Gemessene Performance (14.09.2026, float16 + batch 8, Harness mit TTS-Audio, frischer Prozess)

| Szenario | Audio | Transkription | Echtzeit-Faktor |
|----------|-------|---------------|-----------------|
| Modell laden (Cache) + Warm-up | - | 4.3s + 1.1s | - |
| Kurzes Diktat (12 Woerter) | 5.5s | 0.4s | 14x |
| Langes Diktat (75 Woerter) | 30s | 0.8-1.3s | 24-37x |
| Sehr langes Diktat (270 Woerter) | 105s | 1.9s | 55x |

Log-Statistik vor der Aenderung (1837 `[PERF]`-Zeilen, Feb-Sep 2026): Median 10-14x Echtzeit, Diktate 40-90 s brauchten 3-4 s, ab 90 s 6-9 s (p90 18-20 s). Der PR-#1-Merge selbst hat die Transkription NICHT verlangsamt (identischer `model.transcribe`-Aufruf und Parameter); das Gefuehl «langsamer» kam von den blockierenden Beeps plus 0.3-0.8 s Stream-Erzeugung vor jeder Aufnahme. Auswertung: `.planning/whisper-perf-2026-09-14/report.md` (lokal, nicht im Repo).

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
- **Performance-Update 14.09.2026 (master 5ed9e22 + a313938, deutsch per Merge synchron):** Ausloeser war Danis Eindruck, Whisper sei seit den PR-Merges langsamer. Befund: Transkription unveraendert, aber PR #1 machte beide Beeps blockierend (~300 ms statt ~105 ms) und die Stream-Erzeugung (0.3-0.8 s) lag ohnehin vor jeder Aufnahme. Fix: asynchrone Beeps, vorbereiteter Mikrofon-Stream, Warm-up, `BatchedInferencePipeline` (batch 8) + float16, zweistufiger Halluzinations-Filter («Vielen Dank.» bleibt). Details in den Abschnitten Funktionsweise und Performance-Optimierungen

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
- **whisper-transcribe.py updaten:** Gleiche Settings wie whisper-dictate (vad_filter, Halluzinations-Filter, no_speech deaktiviert, Batched Pipeline)
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
