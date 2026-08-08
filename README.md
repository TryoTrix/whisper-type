# Whisper Type

Local voice-to-text dictation for Windows. Press a hotkey, speak, text appears. Runs fully offline on your NVIDIA GPU with OpenAI's Whisper large-v3, delivering near-perfect accuracy for English, German, and 90+ other languages.

**[Download ZIP](https://github.com/TryoTrix/whisper-type/archive/refs/heads/master.zip)** | Requires Windows + NVIDIA GPU + Python 3.12+

![Demo](demo.gif)

## Features

- **Hotkey dictation:** Press `CTRL+ALT+D`, speak, press again, text gets pasted into the active window
- **Offline & private:** Everything runs locally on your GPU, no audio ever leaves your machine
- **Fast:** 73 seconds of speech transcribed in 7.7 seconds (9.5x real-time on RTX 4060)
- **Accurate:** CUDA float16 with beam search, handles dialects, background music, and long pauses
- **Multi-language:** Works with English, German, and all other Whisper-supported languages
- **Dashboard:** Click the tray icon to see today's stats, recent transcription history with click-to-copy, and quick actions (REC Overlay, restart, quit)
- **Electric Border recording overlay:** Animated microphone icon with dual-ring plasma effect (2D pixel displacement, breathing pulse, core flash), pre-rendered at 30fps. Red pulsing bar across all monitors
- **REC Overlay toggle:** Show or hide the recording overlay from the dashboard. Setting persists across restarts
- **Spoken punctuation:** Say "colon", "question mark" etc. and get the actual character (configurable in `whisper-config.json`)
- **Hallucination filter:** Known Whisper phantom outputs are detected and discarded
- **System tray:** Runs quietly in the background with a color-coded status icon (gray/green/red)
- **Audio feedback:** Beep tones on start/stop so you know when recording begins and ends
- **Silence auto-stop:** Automatically stops a forgotten recording after a configurable period of silence
- **History log:** All transcriptions are saved with timestamps to `whisper-history.log`
- **Autostart:** Launches automatically on Windows login
- **Single file:** The entire tool is one Python script, easy to understand and customize

## Installation

### Prerequisites

- Windows 10/11
- Python 3.12+
- NVIDIA GPU with CUDA support (tested on RTX 4060)
- Up-to-date NVIDIA driver

### Setup

```
git clone https://github.com/TryoTrix/whisper-type.git
cd whisper-type
install.bat
```

The installer will:
1. Check for Python, pip, and NVIDIA GPU
2. Create a project-local virtual environment in `.venv`
3. Install all Python packages into `.venv`
4. Ask whether you want autostart at Windows login
   If enabled, create an autostart entry using `.venv\Scripts\pythonw.exe`
   If disabled, you can launch manually via `manual-launch.bat`
5. Download the Whisper model (~3 GB, one-time)
6. Start the dictation tool

## Uninstall

Run:

```
uninstall.bat
```

The uninstaller will:
1. Remove the autostart Registry Run key (if it exists)
2. Clean Startup leftovers (`Whisper Diktiertool.lnk` and StartupApproved ghost entry)
3. Ask whether to keep local logs/config/history
   If kept, files are left as-is
   If not kept, logs are emptied and `whisper-config.json` is removed
4. Ask whether to keep downloaded Whisper model cache
   If not kept, Whisper model folders in common Hugging Face cache paths are removed
5. Remove project-local `.venv` and Python `__pycache__` folders
6. Optionally remove desktop `Whisper Restart.lnk`

Notes:
- If the app is still running, close it from the tray or Task Manager before uninstalling for complete cleanup.
- Model cache cleanup is targeted to common Whisper model folders, not all Hugging Face assets.

## Usage

| Action | Shortcut |
|--------|----------|
| Start/stop recording | `CTRL+ALT+D` |
| Restart (if hook is lost) | `CTRL+ALT+W` |

**Tray icon colors:**

| Color | Status |
|-------|--------|
| Gray | Model loading (~4s) |
| Green | Ready |
| Red | Recording |

Left-click the tray icon to open the dashboard with stats, history, and actions. Right-click for the context menu.

### Tip: Mouse shortcut

With Razer Synapse (or similar software) you can map `CTRL+ALT+D` to a mouse button, e.g. Hypershift + scroll wheel click. Dictate without touching the keyboard.

## Spoken Punctuation

Say the word, the tool inserts the character. This can be enabled or disabled with `post_processing.apply_spoken_punctuation`; the default mapping uses German words and can be customized in `post_processing.spoken_punctuation` inside `whisper-config.json`.

| Spoken word | Result |
|-------------|--------|
| Doppelpunkt | `: ` |
| Semikolon | `; ` |
| Ausrufezeichen | `!` |
| Fragezeichen | `?` |
| Gedankenstrich | ` - ` |
| Schrägstrich / Slash | `/` |
| Anführungszeichen | `"` |

## Configuration

All user-editable settings live in `whisper-config.json`. The file is structured into sections and is rewritten with indentation when the dashboard persists settings such as `ui.calm_mode` or `ui.rec_overlay`.

| Setting | Description | Default |
|---------|-------------|---------|
| `hotkeys.dictation` | Start/stop recording hotkey | `ctrl+alt+d` |
| `audio.sample_rate` | Microphone sample rate for Whisper | `16000` |
| `audio.beep_volume` | Audio feedback volume, from silent `0.0` to max `1.0` | `0.2` |
| `audio.silence_timeout_seconds` | Stop recording after this many seconds of continuous silence; `0` disables it | `15` |
| `model.size` | Whisper model | `large-v3-turbo` |
| `model.device` | Faster Whisper device | `cuda` |
| `model.compute_type` | Faster Whisper compute type | `int8_float16` |
| `transcription.dictation_language` | Language code passed to `model.transcribe()` | `de` |
| `transcription.beam_size` | Whisper beam search size | `3` |
| `transcription.vad_filter` | Enable faster-whisper VAD | `true` |
| `transcription.condition_on_previous_text` | Reuse previous text as context | `false` |
| `transcription.initial_prompt` | Domain-specific terms for better recognition | Comma-separated list |
| `transcription.no_speech_threshold` | Silence detection threshold | `null` (disabled, VAD handles this) |
| `transcription.short_text_max_words` | Remove trailing period for <= N words | `3` |
| `transcription.debug_transcription` | Write segment details to history log | `true` |
| `post_processing.apply_spoken_punctuation` | Enable spoken punctuation replacement | `true` |
| `post_processing.spoken_punctuation` | Spoken word-to-character regex mapping | See table above |
| `post_processing.word_corrections` | Common Whisper mistake corrections | Regex mapping |
| `post_processing.hallucination_phrases` | Known silence hallucinations to discard | Phrase list |

To switch the language, change `transcription.dictation_language` to your language code, for example `"en"` for English.

## Speed & Accuracy

Uses Whisper `large-v3` with `float16` precision and `beam_size=5` for the best balance of quality and speed. Near-perfect accuracy for both English and German, including dialects and background music.

Benchmarks on RTX 4060:

| Scenario | Audio duration | Transcription time | Real-time factor |
|----------|----------------|-------------------|-----------------|
| Short dictation (1-3 words) | 2-4s | ~0.5s | 4-6x |
| Medium dictation (1-2 sentences) | 4-10s | ~1s | 5-10x |
| Long dictation (6 sentences) | ~55s | ~5s | 11x |
| Very long dictation (20 segments) | 73s | 7.7s | 9.5x |

If you prefer faster transcriptions over maximum accuracy, switch to `large-v3-turbo` with `beam_size=3` (~3-5x faster).

## How It Works

1. **Hotkey** triggers audio recording via `sounddevice`
2. **Audio** is captured as a NumPy array at 16kHz (no WAV file intermediary)
3. **Whisper** transcribes with `faster-whisper` (CTranslate2 backend) on your GPU
4. **Post-processing** applies spoken punctuation replacement and hallucination filtering
5. **Output** is pasted into the active window via clipboard

The recording overlay uses pre-rendered animation frames (90 frames, 30fps) with 2D pixel displacement simulating SVG feDisplacementMap. A dual-ring system (inner plasma ring + outer orbit ring) with independent noise fields creates the electric border effect. All blur layers are pre-composited before the frame loop for minimal CPU usage during recording.

## Platform Compatibility

| Platform | Status | Reason |
|----------|--------|--------|
| Windows 10/11 + NVIDIA GPU | Fully supported | Developed and tested |
| Linux + NVIDIA GPU | Not compatible | Uses Win32 APIs (kernel32, user32, winsound) |
| macOS (Intel/Apple Silicon) | Not compatible | No CUDA support, no Win32 APIs |

The Whisper engine itself (faster-whisper) runs cross-platform, but the integration layer (global hotkey, clipboard, overlay, system tray, audio feedback) is built on Windows APIs.

**Contributions welcome!** If you'd like to port Whisper Type to Linux or macOS, PRs are appreciated. The main components that need platform-specific replacements are:
- Global hotkey listener (`keyboard` library -> e.g. `pynput`)
- Clipboard paste (`pyperclip` + `keyboard.send("ctrl+v")` -> `xdotool`/`pbpaste`)
- System tray icon (`pystray` works cross-platform, minor adjustments needed)
- Recording overlay (tkinter with Win32 click-through -> platform-specific window flags)
- Audio feedback (`winsound.PlaySound` generated WAV -> platform-specific sound API)
- GPU: Linux has CUDA support, macOS would need CoreML or CPU fallback

## System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| OS | Windows 10 | Windows 11 |
| GPU | NVIDIA with CUDA | RTX 3060+ |
| VRAM | 4 GB | 8 GB |
| Python | 3.12+ | 3.12+ |
| RAM | 8 GB | 16 GB |

## Author

Built by Daniel Gächter.

Check out my other projects:
- **[SEO Agent](https://seo-agent.ch)** - SEO services and web development in Switzerland
- **[Lotus Academy](https://nachhilfe-lotusacademy.ch)** - Tutoring school in German-speaking Switzerland

## License

MIT
