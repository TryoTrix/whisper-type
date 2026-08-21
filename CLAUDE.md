# AA Claude Program

> Local AI tools that run on the PC. Python 3.12 + NVIDIA RTX 4060 (CUDA).

---

## Project Structure

| File | Description |
|-------|-------------|
| `whisper-dictate.py` | Dictation tool: speech to text via hotkey, runs as a tray icon |
| `whisper-dictate.bat` | Launcher for whisper-dictate (calls `pythonw`, path-independent via `%~dp0`) |
| `whisper-restart.bat` | Stops running instance and starts it again (kill + wait + start) |
| `whisper-transcribe.py` | Audio file to text (CLI tool, no hotkey) |
| `install.bat` | Setup for new PCs: packages, autostart, model download |
| `uninstall.bat` | Cleanup tool: removes autostart and optionally logs/model cache |
| `whisper-config.json` | ALL settings, versioned (single config source since PR #1, the app does not start without it) |
| `whisper-error.log` | Created on CUDA/model errors (only when an error occurs) |
| `whisper-history.log` | Transcription log: every dictation with timestamp (append, UTF-8) |
| `.claude/skills/pr-review/` | `/pr-review N` skill: read-only PR security scan (`pr-scan.py`) plus the merge, German-branch sync and restart runbook (`SKILL.md`) |

---

## Whisper-Type - Dictation Tool (`whisper-dictate.py`)

### Shortcuts

| Shortcut | Function |
|----------|----------|
| `CTRL+ALT+D` | Start/stop recording |
| `CTRL+ALT+W` | Restart Whisper (kill + start) via desktop shortcut |

### How It Works
- **Hotkey:** `CTRL+ALT+D` starts/stops recording (configured via `hotkeys.dictation` in `whisper-config.json`)
- **Model:** `faster-whisper` large-v3-turbo, language: German by default (configured via `model.*` and `transcription.dictation_language` in `whisper-config.json`)
- **GPU:** CUDA int8_float16 on RTX 4060 (~3 GB VRAM)
- **Transcription:** `beam_size=3`, `vad_filter=True`, `condition_on_previous_text=False` by default, audio is passed directly to Whisper as a NumPy array (no WAV roundtrip). All transcription options live under `transcription` in `whisper-config.json`
- **Initial prompt:** Domain terms Whisper should recognize correctly (e.g. CLAUDE.md). Configurable via `transcription.initial_prompt`, no performance impact
- **Spoken punctuation:** Spoken punctuation is automatically replaced (e.g. "Doppelpunkt" -> `:`, "Fragezeichen" -> `?`, "Anfuehrungszeichen" -> `"`) when `post_processing.apply_spoken_punctuation` is enabled. Mappings are configurable in `post_processing.spoken_punctuation`
- **Output:** Transcribed text is inserted into the active window via clipboard
- **Tray icon colors:** Gray = model loading, Green = ready, Red = recording
- **Tray tooltip stats:** Shows today's dictations and audio duration in the tooltip (e.g. "Today: 5x, 2.1 min"). Updates after each dictation by reading `whisper-history.log`
- **Audio feedback:** High beep (800 Hz) on start, low beep (500 Hz) on stop, plus a ready chime after model load. All are generated as in-memory WAV sounds with `winsound.PlaySound`; volume is controlled by `audio.beep_volume` in `whisper-config.json` (`0.0` silent, `1.0` max)
- **REC overlay:** Red pulsing bar (8px) at top of all monitors during recording (tkinter, click-through). Microphone icon (100x100, 8x supersampling, r_outer=400 for gapless circle) with Electric Border Effect: 90 pre-rendered frames (3s loop, 30fps) using true 2D pixel displacement (simulating SVG feDisplacementMap). Dual-ring system: inner ring (White-hot Core + Sharp + 4 glow layers, border_r=mic_r+1) and outer orbit ring (separate noise field, slower pan). Fill disc (200,42,42, Blur 8) behind all rings fills the full area between mic icon and Electric Border. Noise textures (5 octaves, 520x520) pan circularly for organic turbulence. All blur layers are composited into 2 images BEFORE frame loop (only 2 displacement ops per frame instead of 6; no blur ops in loop). Visual effects: Breathing Pulse (glow intensity via sine), Core Flash (3 short brightness flashes per loop), dark-red compositing (semi-transparent edge pixels -> dark red instead of black). Pre-rendering runs parallel to model load (~5-8s). Fallback: static mic icon with fill disc until frames are ready. ~7 MB RAM for frame list
- **History log:** Every successful transcription is stored with timestamp in `whisper-history.log` (`[2026-02-17 14:32:05] Text...`)

### Configuration (`whisper-config.json`)

| Section | Description |
|---------|-------------|
| `ui` | Dashboard/toggle state such as `calm_mode`, `rec_overlay`, `dashboard_history_entries`, and `preserve_dashboard_history` |
| `logging` | History text persistence (`save_history`) and history file size limit (`max_file_size_mb`) |
| `hotkeys` | Dictation shortcut |
| `audio` | Recording sample rate, beep volume, and `silence_timeout_seconds` (auto-stop after sustained silence; `0` disables it) |
| `model` | Faster Whisper model size, device, compute type, and optional `download_root` (custom Hugging Face cache folder, `null` = default; since PR #2) |
| `transcription` | Language, beam size, VAD, initial prompt, debug logging, short-text punctuation behavior |
| `post_processing` | Spoken punctuation toggle/regexes, word corrections, and hallucination phrase filters |

When the app writes `calm_mode` or `rec_overlay`, it preserves the full config structure and writes readable indented JSON.

### Tray Icon Interaction
- **Left click:** Opens dashboard popup (dark-themed, slide-up animation). Shows status (Ready/Recording/Loading), today's stats (dictations + minutes), the configured number of previous dictations, and action buttons (Calm Mode, Restart, Quit). Closes automatically when recording starts. Toggle behavior: second click closes dashboard
- **Right click:** Native context menu with Calm Mode toggle, Restart, Quit

### Tray Menu (Right Click)
- **Calm Mode:** Toggle (checkmark = enabled). Replaces animated Electric Border overlay with static mic icon (white mic in red circle). Setting is persisted in `whisper-config.json` and applies instantly without restart
- **Restart:** Stops current instance, waits 2s (mutex release), starts `pythonw` directly. Uses `pythonw -c "import time,subprocess;time.sleep(2);..."` instead of `cmd.exe` for fully invisible restart (no terminal window)
- **Quit:** Fully exits dictation tool

### Debug Logging
With `DEBUG_TRANSCRIPTION = True`, each Whisper segment is written to history log with status:
- `KEEP (no_speech=0.12): Text` = Segment kept (no_speech value informational only)
- `SKIP (hallucination): Text` = Known hallucination filtered
- Note: `no_speech_prob` is logged only, not used for filtering (unreliable for German)

### Trailing Period
For short dictations (1-3 words), `remove_trailing_period()` removes the auto-added final period from Whisper. Configurable via `SHORT_TEXT_MAX_WORDS`.

### Performance Metrics
Automatic entries in history log:
- `[STARTUP] Model loaded in 5.2s` = model load time at startup
- `[PERF] 12.3s audio -> 8.1s transcription (1.5x real-time)` = transcription performance per dictation

### Autostart
Starts automatically on Windows login via Registry Run key:
```
HKCU\Software\Microsoft\Windows\CurrentVersion\Run\WhisperDiktiertool
```
- **Value:** `"C:\...\pythonw.exe" "C:\...\whisper-dictate.py"` (dynamic paths)
- **No PowerShell/COM needed:** uses `winreg` (Python stdlib)
- **Setup-managed:** `install.bat` creates or removes the Registry Run key according to the user's autostart choice; the runtime app does not change autostart settings.
- **Cleanup:** old `.lnk` from Startup folder and `StartupApproved` ghost entry are removed automatically

### Start Manually
```
whisper-dictate.bat
```
Or directly: `pythonw whisper-dictate.py`

### Restart (when keyboard hook is lost)
**Option 1:** Right-click tray icon -> "Restart"
**Option 2:** Press `CTRL+ALT+W` (desktop shortcut)
**Option 3:** Manually:
```
whisper-restart.bat
```
Desktop shortcut: `Whisper Restart.lnk` on OneDrive desktop (WindowStyle 7, minimized).
**IMPORTANT:** Do NOT remove this shortcut from the desktop, otherwise CTRL+ALT+W will no longer work (Windows shortcut keys are bound to .lnk files).

### Single Instance
Windows mutex (`WhisperDiktiertool_Mutex`) prevents duplicate startup. If one instance is already running, a second exits immediately.

### Architecture
- **Main thread:** pystray tray icon (blocking), left click sets `_dashboard_toggle` event
- **Thread 1:** `hotkey_loop` - waits on `keyboard.wait(HOTKEY)`, starts only when model is loaded
- **Thread 2:** `load_model` - loads Whisper model on GPU
- **Thread 3:** `RecordingOverlay` - tkinter windows, polls `recording` every 100ms
- **Thread 4:** `_prerender_frames` - renders 90 Electric Border frames at startup (parallel with thread 2+3)

### CUDA DLL Paths
The script manually sets NVIDIA DLL paths for cublas and cudnn:
```
Python312/Lib/site-packages/nvidia/cublas/bin
Python312/Lib/site-packages/nvidia/cudnn/bin
```

---

## Model Decisions (tested 2026-02-19, switched 2026-03-06)

| Model | Result | Recommendation |
|--------|----------|------------|
| `large-v3` + float16 + beam_size=5 | Best quality, including background music. Slower (~12-16s for 5 sentences) | Maximum quality, but too slow for daily use |
| `large-v3-turbo` + int8_float16 + beam_size=3 | Good quality, much faster (~3-5s). Balanced transcription speed and quality | **Currently active** - best speed/quality compromise |
| `distil-large-v3` | Transcribed German as English, even with `language="de"`. Unusable for German | Do not use |
| `TheChola/whisper-large-v3-turbo-german-faster-whisper` | Gated HuggingFace repo, requires account + token. 2.6% WER on German. Not tested | Test with HF login if needed |

### NPU (Intel Movidius 3700VC in Surface Laptop Studio 2)
- Not usable for Whisper: OpenVINO dropped Movidius support after v2022.3
- Even modern Intel Core Ultra NPUs (10-48 TOPS) are much slower than RTX 4060 (194 TOPS)
- RTX 4060 with CUDA remains the best option

### Newer Models (as of Feb 2026)
- No Whisper v4 released or announced
- OpenAI focus is on cloud-only models (gpt-4o-transcribe)
- `large-v3-turbo` (October 2024) is the newest open-source model

---

## Troubleshooting

### CTRL+ALT+D does not respond
1. **Process stuck:** Open Task Manager, end `pythonw.exe`, restart `whisper-dictate.bat`
2. **Model not loaded:** Check tray icon; if gray instead of green, model is not loaded. Check if `whisper-error.log` exists
3. **CUDA error:** Read `whisper-error.log` in project folder. Common causes: GPU busy by another process, driver update needed
4. **Keyboard hook lost:** After sleep/wake, Windows updates, or long runtime (~3h+), low-level keyboard hook may be lost. Press `CTRL+ALT+W` to restart

### Known Behaviors
- `pythonw` has no console: errors are invisible. Model load errors are written to `whisper-error.log`
- `hotkey_loop` thread waits forever for `model is not None`. If model cannot load, hotkey never responds
- `keyboard` library may need admin rights for global hotkeys (depends on Windows version/settings)
- RAM usage of ~228 MB is normal (~220 MB base + ~7 MB Electric Border frames). Model lives in GPU VRAM, not system RAM
- Whisper's `no_speech_prob` is unreliable for German: clear spoken sentences can be marked as 0.97. Therefore filtering is disabled (`NO_SPEECH_THRESHOLD = None`). `vad_filter=True` handles silence detection at audio level
- Whisper can transcribe number formatting inconsistently (e.g. "140" as "140.000" in German thousands format). This is a model limitation

---

## Performance Optimizations (completed)

| Optimization | Effect |
|-------------|--------|
| Removed WAV roundtrip (direct NumPy array to Whisper) | Faster transcription, less I/O |
| `float16` compute_type | Maximum quality on RTX 4060 |
| `beam_size=5` | Best results, slightly slower than beam_size=3 |
| `condition_on_previous_text=False` | Lower context overhead |
| Preview feature removed entirely | No GPU contention, no 0-3s wait for preview thread stop |
| Mic icon 8x supersampling (instead of 4x) | Smoother edges despite tkinter 1-bit transparency |
| Composite edges against dark red (instead of black) | Semi-transparent edge pixels become dark red instead of near-black |
| Convert segment generator to list (`list(segments)`) | Prevents data loss on iteration errors |
| Electric Border pre-rendered (90 frames) | Zero render cost at runtime, only frame index updates (<1ms) |
| 2D pixel displacement instead of polyline noise | True feDisplacementMap-like result instead of "worm" effect |
| Audio level tracking (`audio_level` global) | RMS level computed in `audio_callback` (0.0-1.0), not yet used visually |
| Pre-composite all blur layers before frame loop | 2 displacement ops per frame instead of 6, 0 blur ops in loop (42s -> ~5-8s) |
| Dual-ring system (inner + outer orbit) | Outer ring with separate noise field and slower pan adds depth |
| White-hot core + breathing pulse + core flash | Plasma core (near-white), glow pulses by sine, 3 flashes per loop |
| Dark-red compositing for Electric Border | Semi-transparent glow edge pixels -> dark red instead of near-black |
| Restart without CMD window | `pythonw -c` instead of `cmd.exe /c timeout` for fully invisible restart |
| Fill disc behind electric rings | Filled red circle (200,42,42, Blur 8) fills gap between mic and ring |
| Mic icon r_outer 384->400, border_r +6->+1 | Red circle fully fills icon, ring sits directly at edge |
| no_speech_prob filtering disabled | No more lost segments (Whisper marked clear speech with 0.97) |

### Measured Performance (2026-02-22)

| Scenario | Audio | Transcription | Real-time factor |
|----------|-------|---------------|-----------------|
| Model load (cold start) | - | 5.7s | - |
| Model load (cache) | - | 3.2s | - |
| Short dictation (1-3 words) | 2-4s | 0.5-0.6s | 4-6x |
| Medium dictation (1-2 sentences) | 4-10s | 0.7-1.2s | 5-10x |
| Long dictation (6 sentences) | 54.6s | 5.0s | 11x |
| Very long dictation (20 segments) | 72.8s | 7.7s | 9.5x |

---

## Python Dependencies

| Package | Purpose |
|-------|-------|
| `faster-whisper` 1.2.1 | Whisper speech-to-text (CTranslate2 backend) |
| `sounddevice` | Microphone audio capture |
| `keyboard` | Global hotkey (low-level hook) |
| `pyperclip` | Clipboard access for text insertion |
| `pystray` | System tray icon |
| `Pillow` | Icon generation for pystray |
| `numpy` | Audio data processing |
| `nvidia-cublas-cu*` | CUDA library (GPU acceleration) |
| `nvidia-cudnn-cu*` | CUDA Deep Neural Network library |

### Installation (manual)
```
pip install faster-whisper sounddevice keyboard pyperclip pystray Pillow
```
CUDA/cuDNN are installed automatically with `faster-whisper`.

### Installation (new PC)
Copy folder and run `install.bat`. Script does:
1. Checks Python, pip, and NVIDIA GPU
2. Creates a project-local virtual environment in `.venv`
3. Installs all pip packages into `.venv`
4. Asks whether autostart should be enabled
   If enabled, creates autostart via Registry Run key (HKCU)
   If disabled, use `manual-launch.bat` after login to start manually
5. Downloads Whisper model (~3 GB for large-v3, first start)
6. Starts dictation tool

Requirements: Python 3.12+ and NVIDIA GPU with current driver.

### Uninstall / Cleanup
Run `uninstall.bat` from the project folder.

What it does:
1. Removes the `WhisperDiktiertool` Run key from HKCU (if present)
2. Asks whether to keep local data files (logs/config/history)
3. Asks whether to keep downloaded Whisper model cache
4. Removes project-local `.venv` and Python `__pycache__` folders
5. Optionally removes desktop `Whisper Restart.lnk`

---

## Whisper Transcription (`whisper-transcribe.py`)

CLI tool for longer audio files:
```
python whisper-transcribe.py "path/to/audiofile.mp3"
```
- Creates `.txt` (full text) and `.srt` (subtitles) next to source file
- Supported formats: mp3, wav, m4a, flac, ogg, wma, aac, mp4, mkv, avi
- Same GPU settings as whisper-dictate

---

## GitHub

- **Public repo:** `TryoTrix/whisper-type` (https://github.com/tryotrix/whisper-type)
- **Branches:** `master` = English upstream (this file). `deutsch` = German working version used on the maintainer's PC (German UI strings, German CLAUDE.md, `whisper-transcribe.py` defaults to `de`, Swiss `ß → ss` word correction plus personal config values). Rule: every change lands on master first, then `git merge master` into `deutsch`; `git log deutsch..master` must stay empty
- **gh CLI:** not installed. PRs are reviewed with the `/pr-review N` skill (`.claude/skills/pr-review/`): `pr-scan.py` fetches the PR head into a local `pr-N` branch (never checked out, nothing executed) and scans metadata, links, hidden unicode, dangerous code patterns and prompt-injection phrases; `SKILL.md` then covers the manual checklist, the verdict, the merge in a temporary worktree, the German-branch sync, restart and test
- **PR #1 (merged 2026-08-14 as df7f5db):** external contributor vousk, 23 commits, +1250/-686: English translation of docs/UI/logs, `whisper-config.json` as the single config source, venv-based install, `uninstall.bat`, silence auto-stop, log rotation, beep volume, privacy mode. Security review before the merge: clean. The faulty "Punkt" regex from the PR config was removed right after the merge (4b2b584)
- **PR #2 (merged 2026-08-21):** external contributor tkhyn, 1 line: optional `model.download_root` passed to `WhisperModel`. Security review: clean. Bug fixed in the follow-up commit: the original line passed `str(None)` = `"None"` as `cache_dir`, which would have re-downloaded the model into a folder named `None` for every user without the key

---

## Future Ideas

- **Move SPOKEN_PUNCTUATION to config:** store in `whisper-config.json` instead of hardcoded in code, add mappings without script edits
- **Update whisper-transcribe.py:** same settings as whisper-dictate (vad_filter, hallucination filtering, no_speech disabled)
- **Auto-reconnect keyboard hook:** watchdog thread detects hook loss after ~3h/sleep and re-registers automatically
- **Switchable language:** tray menu toggle between German/English, or second hotkey (e.g. CTRL+ALT+E for English)

---

## System Environment

- **Python:** 3.12.0
- **GPU:** NVIDIA GeForce RTX 4060 (8 GB VRAM)
- **NPU:** Intel Movidius 3700VC VPU (not usable for Whisper)
- **CUDA:** 13.1, driver 591.74
- **OS:** Windows 11
- **Device:** Surface Laptop Studio 2
- **Model cache:** `~\\.cache\\huggingface\\hub\\`
