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
| `whisper-server.py` | Local HTTP transcription server for the LAN (since 2026-09-15): `POST /jobs` upload → `GET /jobs/<id>` text + segment + word timestamps, `POST /transcribe` synchronous, CORS, optional `--token`, `--device cpu --compute-type int8 --threads N` to simulate a CPU server. Standard library only; reuses `whisper-config.json` (model, transcription, post-processing) and the two-tier hallucination filter. Built for the Mappe project (phone app upload flow) |
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
- **GPU:** CUDA float16 on RTX 4060 (~2 GB VRAM). Switched from int8_float16 on 2026-09-14: float16 measured 10-25% faster on this GPU, loads faster (no quantization at start) and has no quantization loss
- **Transcription:** `beam_size=3`, `vad_filter=True`, `condition_on_previous_text=False` by default, audio is passed directly to Whisper as a NumPy array (no WAV roundtrip). All transcription options live under `transcription` in `whisper-config.json`
- **Batched decoding (since 2026-09-14):** `transcription.batch_size` (default 8, `0`/`1` = sequential) uses faster-whisper's `BatchedInferencePipeline`: the VAD splits the recording at pauses and the chunks are decoded in parallel. Measured on RTX 4060 with TTS audio: 30 s in 0.8-1.3 s instead of 2.0 s, 105 s in 1.9 s instead of 7.0 s, word error rate equal or better. Requires `vad_filter=true`; otherwise the sequential path is used. `[PERF]` lines end with `, batch 8` when the batched path was used
- **Warm-up:** `load_model()` transcribes two seconds of silence (batched, 2 clips, no VAD) before publishing the model, so the first dictation after a start is no longer ~1 s slower. Logged as `[STARTUP] Model loaded in 4.3s (warm-up 1.1s)`
- **Microphone stream (since 2026-09-14):** Creating an `sd.InputStream` costs 0.3-0.8 s on the MME host API, so a stream is created ahead of time (`prepare_input_stream()`, at startup and again in a background thread after every dictation) and only started on the hotkey (~1 ms, first audio after ~150 ms). A created-but-stopped stream does not count as microphone use for Windows (verified via `CapabilityAccessManager` timestamps), so the privacy indicator only lights while recording. Recreating the stream after each dictation also picks up a changed default microphone; if `start()` fails, a fresh stream is created once more before giving up with a tray message
- **Initial prompt:** Domain terms Whisper should recognize correctly (e.g. CLAUDE.md). Configurable via `transcription.initial_prompt`, no performance impact
- **Spoken punctuation:** Spoken punctuation is automatically replaced (e.g. "Doppelpunkt" -> `:`, "Fragezeichen" -> `?`, "Anfuehrungszeichen" -> `"`) when `post_processing.apply_spoken_punctuation` is enabled. Mappings are configurable in `post_processing.spoken_punctuation`
- **Output:** Transcribed text is inserted into the active window via clipboard (`paste_text()`). The previous clipboard content comes back after `ui.clipboard_restore_delay_seconds` (default 3 s) and only if nothing else changed the clipboard in the meantime (`GetClipboardSequenceNumber`). Until 2026-09-15 this was a fixed 150 ms: a busy target window (a terminal rendering streamed output) read the clipboard after that and pasted the OLD text. Before Ctrl+V the tool waits up to 0.5 s for the hotkey's Ctrl/Alt to be released (otherwise the window receives Ctrl+Alt+V)
- **Tray icon colors:** Gray = model loading, Green = ready, Red = recording
- **Tray tooltip stats:** Shows today's dictations and audio duration in the tooltip (e.g. "Today: 5x, 2.1 min"). Updates after each dictation by reading `whisper-history.log`
- **Audio feedback:** High beep (800 Hz) on start, low beep (500 Hz) on stop, plus a ready chime after model load. The sounds are rendered once into temp WAV files (`%TEMP%\whisper-type-*.wav`) and played with `winsound.PlaySound(SND_FILENAME | SND_ASYNC)`, i.e. without blocking: PR #1's in-memory `SND_MEMORY` playback cannot be asynchronous and blocked the hotkey thread ~300 ms per beep (measured; `winsound.Beep` before PR #1 blocked ~105 ms). Audio captured during the start beep (`BEEP_DURATION_MS` + 30 ms) is dropped in `audio_callback` so the beep is never transcribed. Volume via `audio.beep_volume` (`0.0` silent, `1.0` max); fallback to blocking in-memory playback if the temp file cannot be written
- **REC overlay:** Red pulsing bar (8px) at top of all monitors during recording (tkinter, click-through). Microphone icon (100x100, 8x supersampling, r_outer=400 for gapless circle) with Electric Border Effect: 90 pre-rendered frames (3s loop, 30fps) using true 2D pixel displacement (simulating SVG feDisplacementMap). Dual-ring system: inner ring (White-hot Core + Sharp + 4 glow layers, border_r=mic_r+1) and outer orbit ring (separate noise field, slower pan). Fill disc (200,42,42, Blur 8) behind all rings fills the full area between mic icon and Electric Border. Noise textures (5 octaves, 520x520) pan circularly for organic turbulence. All blur layers are composited into 2 images BEFORE frame loop (only 2 displacement ops per frame instead of 6; no blur ops in loop). Visual effects: Breathing Pulse (glow intensity via sine), Core Flash (3 short brightness flashes per loop), dark-red compositing (semi-transparent edge pixels -> dark red instead of black). Pre-rendering runs parallel to model load (~5-8s). Fallback: static mic icon with fill disc until frames are ready. ~7 MB RAM for frame list
- **History log:** Every successful transcription is stored with timestamp in `whisper-history.log` (`[2026-02-17 14:32:05] Text...`)

### Configuration (`whisper-config.json`)

| Section | Description |
|---------|-------------|
| `ui` | Dashboard/toggle state such as `calm_mode`, `rec_overlay`, `dashboard_history_entries`, and `preserve_dashboard_history`, plus `clipboard_restore_delay_seconds` (3.0, optional since 2026-09-15, 0 = the dictation stays in the clipboard) |
| `logging` | History text persistence (`save_history`) and history file size limit (`max_file_size_mb`) |
| `hotkeys` | Dictation shortcut |
| `audio` | Recording sample rate, beep volume, and `silence_timeout_seconds` (auto-stop after sustained silence; `0` disables it) |
| `model` | Faster Whisper model size, device, compute type, and optional `download_root` (custom Hugging Face cache folder, `null` = default; since PR #2) |
| `transcription` | Language, beam size, optional `batch_size` (batched decoding, default 8), VAD, initial prompt, debug logging, short-text punctuation behavior |
| `post_processing` | Spoken punctuation toggle/regexes, word corrections, and hallucination filters: `hallucination_phrases` (always dropped), optional `hallucination_patterns` (regexes, always dropped), optional `hallucination_phrases_low_confidence` + `hallucination_logprob_threshold` (-1.0): everyday phrases like "vielen dank" are only dropped when the segment's `avg_logprob` is below the threshold. Before 2026-09-14 a real "Vielen Dank." at the end of a dictation was deleted every time (12 cases in the log) |

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
- `KEEP (no_speech=0.12, logprob=-0.30): Text` = Segment kept (no_speech value informational only, logprob = Whisper confidence)
- `SKIP (hallucination, logprob=-0.90): Text` = Known hallucination filtered (phrase list or regex)
- `SKIP (low-confidence phrase, logprob=-1.60): Text` = Everyday phrase (e.g. "Vielen Dank.") dropped because Whisper was unsure
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
| `large-v3-turbo` + int8_float16 + beam_size=3 | Good quality, much faster (~3-5s). Balanced transcription speed and quality | Active 2026-03-06 to 2026-09-14 |
| `large-v3-turbo` + float16 + beam_size=3 + batch_size=8 | Same quality as int8_float16 on TTS test audio (WER 0-2%), 10-25% faster per chunk, plus 2-3x on long dictations from batched decoding (2026-09-14) | **Currently active** |
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

### Previous message pasted instead of the new dictation (fixed 2026-09-15)
Symptom: the transcription in the log is correct, but the window shows the previous dictation. Cause: the old clipboard content was restored a fixed 150 ms after Ctrl+V; a busy target window (Claude Code in Windows Terminal while an answer is streaming) processed the Ctrl+V later and pasted the already restored old content. Evidence: the Windows clipboard history (Win+V), read via WinRT with timestamps, showed the previous dictation in the clipboard right after each affected dictation. Fix: `paste_text()` restores only after `ui.clipboard_restore_delay_seconds` (3 s) and only if the clipboard sequence number is unchanged. The log shows the outcome as `[DEBUG] Clipboard restored after 3.0s` or `... restore skipped: clipboard changed by another app`; `[DEBUG] Paste waited ...` reports modifier keys that were still held.

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
| Asynchronous beeps from temp WAV files (2026-09-14) | Hotkey thread no longer blocks ~300 ms per beep (PR #1 regression); stop beep no longer delays transcription |
| Prepared microphone stream (2026-09-14) | Mic capture starts ~150 ms after the hotkey instead of 0.6-1.1 s; fewer clipped first words |
| Model warm-up at startup (2026-09-14) | First dictation per session no longer ~1 s slower (log: first-dictation median 5.6x vs. 13.5x real-time before) |
| `BatchedInferencePipeline` + float16 (2026-09-14) | 30 s audio 2.0 s -> 0.8-1.3 s, 105 s audio 7.0 s -> 1.9 s, same WER |

### Measured Performance (2026-09-14, float16 + batch 8, harness with TTS audio, fresh process)

| Scenario | Audio | Transcription | Real-time factor |
|----------|-------|---------------|-----------------|
| Model load (cache) + warm-up | - | 4.3s + 1.1s | - |
| Short dictation (12 words) | 5.5s | 0.4s | 14x |
| Long dictation (75 words) | 30s | 0.8-1.3s | 24-37x |
| Very long dictation (270 words) | 105s | 1.9s | 55x |

Log statistics before the change (1837 `[PERF]` lines, Feb-Sep 2026): median 10-14x real-time, 40-90 s dictations 3-4 s, 90 s+ dictations 6-9 s (p90 18-20 s). The PR #1 merge itself did not slow transcription down (identical `model.transcribe` call and parameters); the felt slowness came from the blocking beeps plus the 0.3-0.8 s microphone stream creation before each recording.

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
