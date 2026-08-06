"""
Whisper Diktiertool - Dictation Tool - Speak & Insert Text
============================================
Press CTRL+ALT+D to start/stop recording.
Runs as a system tray icon (no taskbar entry).
Only one instance can run at a time (mutex protected).

Start:
    pythonw whisper-dictate.py
"""

import sys
import os
import ctypes
from ctypes import wintypes

# Single instance: Windows mutex prevents duplicate launches
_mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "WhisperDiktiertool_Mutex")
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)

# Make NVIDIA DLLs visible for CUDA (cublas, cudnn)
_nvidia_base = os.path.join(
    os.path.dirname(sys.executable), "Lib", "site-packages", "nvidia"
)
_dll_dirs = []
for _lib in ("cublas", "cudnn"):
    _dll_dir = os.path.join(_nvidia_base, _lib, "bin")
    if os.path.isdir(_dll_dir):
        os.add_dll_directory(_dll_dir)
        _dll_dirs.append(_dll_dir)
# Also extend PATH (CTranslate2 loads DLLs via LoadLibrary)
if _dll_dirs:
    os.environ["PATH"] = os.pathsep.join(_dll_dirs) + os.pathsep + os.environ.get("PATH", "")

import time
import math
import re
import json
import threading
import numpy as np
import sounddevice as sd
import keyboard
import pyperclip
import subprocess
import pystray
from PIL import Image, ImageDraw, ImageFilter
import winsound

# ============================================================
# CONFIGURATION - Edit here
# ============================================================
HOTKEY = "ctrl+alt+d"
SAMPLE_RATE = 16000      # Whisper expects 16kHz
MODEL_SIZE = "large-v3-turbo"  # Faster (~3-5s instead of ~12-16s), good quality
NO_SPEECH_THRESHOLD = None  # Disabled (no_speech_prob is unreliable for German)
DEBUG_TRANSCRIPTION = True   # Write segment details to history log
SHORT_TEXT_MAX_WORDS = 3     # For <= N words: remove trailing period

# Domain terms Whisper should recognize correctly (biases decoder, no performance impact)
INITIAL_PROMPT = "CLAUDE.md, Whisper, faster-whisper, Python, CUDA, RTX 4060, committe, pushe, Punkt, YOLO, TryoTrix, CMD, committen, pushen, Commit, Push"

# Spoken punctuation -> real characters (regex patterns, case-insensitive)
# Commas/spaces before and after the word are consumed as part of the match
SPOKEN_PUNCTUATION = {
    r'[,\s]*[-–]?\s*Doppelpunkt[,\s]*': ': ',
    r'[,\s]*[-–]?\s*Semikolon[,\s]*': '; ',
    r'[,\s]*[-–]?\s*Ausrufezeichen': '!',
    r'[,\s]*[-–]?\s*Fragezeichen': '?',
    r'[,\s]*[-–]?\s*Gedankenstrich[,\s]*': ' - ',
    r'[,\s]*[-–]?\s*(?:Schrägstrich|Slash)[,\s]*': '/',
    r'[,\s]*[-–]?\s*Anführungszeichen[,\s]*': '"',
    r'[,\s]*[-–]?\s*Punkt': '.',
}

# Word corrections: common Whisper mistakes -> correct spelling (regex, case-insensitive)
WORD_CORRECTIONS = {
    r'\bTrial[\s-]?Tricks?\b': 'TryoTrix',
    r'\bTry[\s-]?o[\s-]?Tricks?\b': 'TryoTrix',
    r'\bTryo[\s-]?Tricks?\b': 'TryoTrix',
    r'\bTry[\s-]?your[\s-]?Tricks?\b': 'TryoTrix',
    r'\bTriotricks?\b': 'TryoTrix',
    r'\bTryotricks?\b': 'TryoTrix',
}

# Known Whisper hallucinations in silence (lowercase for matching)
HALLUCINATION_PHRASES = {
    "untertitelung von zdf",
    "untertitel von zdf",
    "untertitelung des zdf",
    "untertitel des zdf",
    "untertitel der amara.org-community",
    "copyright wdr",
    "copyright swr",
    "vielen dank fürs zuschauen",
    "vielen dank für's zuschauen",
    "danke fürs zuschauen",
    "thanks for watching",
    "thank you for watching",
    "bis zum nächsten mal",
    "ich danke euch fürs zuschauen",
    "tschüss",
    "vielen dank.",
    "vielen dank",
    "untertitelung des zdf, 2020",
    "untertitelung des zdf 2020",
}
# ============================================================

# Win32 API for window management
user32 = ctypes.windll.user32

# Globale Variablen
recording = False
audio_chunks = []
audio_overflow_count = 0
audio_level = 0.0  # RMS level 0.0-1.0, updated in audio_callback
model = None
stream = None
target_window = None
tray_icon = None
calm_mode = False  # True = static mic icon instead of Electric Border
_dashboard_toggle = threading.Event()  # Signal from tray (left click) to tkinter thread


def create_icon_idle():
    """Green icon = ready."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill="#22c55e")
    return img


def create_icon_recording():
    """Red icon = recording in progress."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill="#ef4444")
    return img


def create_icon_loading():
    """Gray icon = model loading."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill="#9ca3af")
    return img


def get_today_stats():
    """Count today's dictations and sum audio duration from whisper-history.log."""
    try:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        log_path = os.path.join(os.path.dirname(__file__), "whisper-history.log")
        if not os.path.exists(log_path):
            return 0, 0.0
        count = 0
        total_seconds = 0.0
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                # Only real dictations: [2026-02-23 15:47:45] (14.8s) Text...
                if not line.startswith(f"[{today}"):
                    continue
                m = re.match(r'\[.+?\] \((\d+\.?\d*)s\) .+', line)
                if m:
                    count += 1
                    total_seconds += float(m.group(1))
        return count, total_seconds
    except Exception:
        return 0, 0.0


def get_recent_logs(max_entries=20):
    """Get recent dictations from whisper-history.log for the dashboard.

    Filters out DEBUG/PERF/STARTUP lines and parses only real dictations.
    Reads only the last 500 lines for performance on large log files.
    """
    log_path = os.path.join(os.path.dirname(__file__), "whisper-history.log")
    if not os.path.exists(log_path):
        return []
    entries = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        for line in lines[-500:]:
            if any(tag in line for tag in ("[DEBUG]", "[PERF]", "[STARTUP]", "[FEHLER]", "OVERFLOW")):
                continue
            # New format with duration: [2026-02-23 14:32:05] (12.3s) Text...
            m = re.match(r'\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] \((\d+\.?\d*)s\) (.+)', line)
            if m:
                entries.append({
                    "date": m.group(1), "time": m.group(2),
                    "duration": float(m.group(3)), "text": m.group(4).strip()
                })
                continue
            # Old format without duration: [2026-02-17 23:47:02] Text...
            m = re.match(r'\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] (.+)', line)
            if m:
                text = m.group(3).strip()
                if not text.startswith("["):
                    entries.append({
                        "date": m.group(1), "time": m.group(2),
                        "duration": 0, "text": text
                    })
    except Exception:
        pass
    return entries[-max_entries:]


def update_tray(status_text, icon_img):
    """Update tray icon and tooltip."""
    if tray_icon:
        tray_icon.icon = icon_img
        count, total_sec = get_today_stats()
        stats = ""
        if count > 0:
            minutes = total_sec / 60
            stats = f" | Heute: {count}x, {minutes:.1f} Min"
        tray_icon.title = f"Whisper Diktiertool - {status_text}{stats}"


def play_start_sound():
    """Short high beep = recording started."""
    winsound.Beep(800, 100)


def play_stop_sound():
    """Short low beep = recording stopped."""
    winsound.Beep(500, 100)


def play_ready_sound():
    """Soft ready chime after model load (startup only)."""
    try:
        sr = 44100
        # Two ascending notes: G5 -> C6 (soft "ding-ding")
        t1 = np.linspace(0, 0.12, int(sr * 0.12), False)
        t2 = np.linspace(0, 0.25, int(sr * 0.25), False)
        note1 = np.sin(2 * np.pi * 784 * t1) * np.exp(-t1 * 12)  # G5, kurz
        note2 = np.sin(2 * np.pi * 1047 * t2) * np.exp(-t2 * 6)  # C6, lingering tail
        gap = np.zeros(int(sr * 0.04))  # 40ms pause
        chime = np.concatenate([note1, gap, note2]) * 0.15  # Quiet
        sd.play(chime.astype(np.float32), sr)
    except Exception:
        pass  # Sound is nice-to-have, never crash on issues


def filter_hallucinations(segments):
    """Filter Whisper hallucinations (silence phantoms and known phrases)."""
    filtered = []
    debug_lines = []
    for seg in segments:
        text = seg.text.strip()
        no_speech = getattr(seg, "no_speech_prob", 0.0)
        # no_speech_prob filtering disabled: in German, Whisper often returns 0.97
        # for clearly spoken sentences. vad_filter=True already performs audio VAD.
        if NO_SPEECH_THRESHOLD is not None and no_speech > NO_SPEECH_THRESHOLD:
            if DEBUG_TRANSCRIPTION:
                debug_lines.append(f"  SKIP (no_speech={no_speech:.2f}): {text}")
            continue
        if not text:
            continue
        # Check known hallucinations
        text_lower = text.lower().rstrip(".!?,;:")
        if text_lower in HALLUCINATION_PHRASES:
            if DEBUG_TRANSCRIPTION:
                debug_lines.append(f"  SKIP (hallucination): {text}")
            continue
        if DEBUG_TRANSCRIPTION:
            debug_lines.append(f"  KEEP (no_speech={no_speech:.2f}): {text}")
        filtered.append(text)
    # Write debug info to log
    if DEBUG_TRANSCRIPTION and debug_lines:
        append_to_history("[DEBUG] Segmente:\n" + "\n".join(debug_lines))
    return filtered


def apply_spoken_punctuation(text):
    """Replace spoken punctuation with real symbols."""
    for pattern, replacement in SPOKEN_PUNCTUATION.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r'  +', ' ', text)  # Collapse repeated spaces
    return text.strip()


def apply_word_corrections(text):
    """Replace Whisper mistakes with corrected spelling."""
    for pattern, replacement in WORD_CORRECTIONS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def remove_trailing_period(text):
    """Remove trailing period for short texts (1-3 words)."""
    if len(text.split()) <= SHORT_TEXT_MAX_WORDS and text.endswith('.'):
        return text[:-1]
    return text


def append_to_history(text, duration=0):
    """Save transcription with timestamp and duration in whisper-history.log."""
    try:
        from datetime import datetime
        log_path = os.path.join(os.path.dirname(__file__), "whisper-history.log")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        dur_str = f" ({duration:.1f}s)" if duration > 0 else ""
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}]{dur_str} {text}\n")
    except Exception:
        pass


def load_config():
    """Load config from whisper-config.json. Falls back to defaults if missing."""
    global calm_mode
    config_path = os.path.join(os.path.dirname(__file__), "whisper-config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        calm_mode = config.get("calm_mode", False)
    except Exception:
        pass


def save_config():
    """Save current config to whisper-config.json."""
    config_path = os.path.join(os.path.dirname(__file__), "whisper-config.json")
    try:
        config = {"calm_mode": calm_mode}
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)
    except Exception:
        pass


def ensure_autostart():
    """Create/update Registry Run key and clean up old .lnk artifacts."""
    import winreg
    try:
        # Resolve paths dynamically
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        script = os.path.abspath(__file__)
        expected_value = f'"{pythonw}" "{script}"'

        # Check Registry Run key and set if needed
        reg_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        reg_name = "WhisperDiktiertool"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                current_value, _ = winreg.QueryValueEx(key, reg_name)
                if current_value == expected_value:
                    # Already correct, only verify cleanup
                    _cleanup_old_autostart()
                    return
            except FileNotFoundError:
                pass  # Entry does not exist yet
            winreg.SetValueEx(key, reg_name, 0, winreg.REG_SZ, expected_value)

        # Clean up old .lnk and StartupApproved entry
        _cleanup_old_autostart()
    except Exception:
        pass


def _cleanup_old_autostart():
    """Remove old startup shortcut and StartupApproved ghost entry."""
    import winreg
    # Delete old .lnk
    try:
        startup_dir = os.path.join(os.environ["APPDATA"],
                                   "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        lnk_path = os.path.join(startup_dir, "Whisper Diktiertool.lnk")
        if os.path.exists(lnk_path):
            os.remove(lnk_path)
    except Exception:
        pass
    # Remove StartupApproved ghost entry (prevents dead entry in Task Manager)
    try:
        approved_key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_key, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "Whisper Diktiertool.lnk")
    except Exception:
        pass


def get_monitors():
    """Enumerate all connected monitors (position and size)."""
    monitors = []

    MonitorEnumProc = ctypes.WINFUNCTYPE(
        ctypes.c_bool,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.POINTER(wintypes.RECT),
        ctypes.c_ulong,
    )

    def callback(hMonitor, hdcMonitor, lprcMonitor, dwData):
        rect = lprcMonitor.contents
        monitors.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(callback), 0)
    return monitors


class RecordingOverlay:
    """Microphone icon with pre-rendered Electric Border effect + red bar."""

    BAR_HEIGHT = 8
    DISPLAY_SIZE = 160   # Window size (100px mic + room for Electric Border + glow)
    RENDER_SIZE = 320    # 2x supersampling
    MIC_DISPLAY = 100    # Display size of mic icon
    TRANS_COLOR = (1, 1, 1)  # RGB key color for transparency
    NUM_FRAMES = 90      # 3s Animation-Loop bei 30fps

    def __init__(self):
        self.root = None
        self._windows = []
        self._orb_win = None
        self._orb_label = None
        self._orb_photo = None
        self._mic_rgba = None       # Pre-rendered mic icon (RGBA, render size)
        self._frames = None         # List of pre-rendered RGB frames
        self._frames_ready = False  # True when pre-rendering is complete
        self._static_frame = None   # Fallback frame (mic only, no Electric Border)
        self._visible = False
        self._t0 = 0
        self._dashboard_win = None
        self._dashboard_visible = False

    def _create_mic_icon(self):
        """Original microphone icon as RGBA, 8x supersampling for smooth edges.

        Renders at 800x800 and scales to render size (200x200).
        Same design as the previous icon (red gradient circle, white mic).
        """
        hs = 800
        img = Image.new("RGBA", (hs, hs), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = 400

        # --- Circle: smooth gradient (many steps) ---
        r_outer = 400  # Full size, fills icon bounding box completely
        steps = 40
        for i in range(steps):
            t = i / (steps - 1)
            inset = int(16 + t * 176)
            r = int(205 + t * 34)
            g = int(45 + t * 23)
            b = int(45 + t * 23)
            draw.ellipse([cx - r_outer + inset, cx - r_outer + inset,
                          cx + r_outer - inset, cx + r_outer - inset],
                         fill=(r, g, b))

        # --- Glossy highlight (upper half, subtle) ---
        highlight = Image.new("RGBA", (hs, hs), (0, 0, 0, 0))
        hdraw = ImageDraw.Draw(highlight)
        hdraw.ellipse([160, 72, hs - 160, cx + 20], fill=(255, 255, 255, 30))
        img = Image.alpha_composite(img, highlight)
        draw = ImageDraw.Draw(img)

        # --- Microphone ---
        w = (255, 255, 255)

        # Shadow
        sh = Image.new("RGBA", (hs, hs), (0, 0, 0, 0))
        shd = ImageDraw.Draw(sh)
        o = 10
        sc = (120, 25, 25, 100)
        shd.rounded_rectangle([cx-76+o, 176+o, cx+76+o, 432+o], radius=76, fill=sc)
        shd.arc([cx-136+o, 352+o, cx+136+o, 544+o], 0, 180, fill=sc, width=24)
        shd.line([cx+o, 544+o, cx+o, 600+o], fill=sc, width=24)
        shd.rounded_rectangle([cx-68+o, 588+o, cx+68+o, 616+o], radius=14, fill=sc)
        img = Image.alpha_composite(img, sh)
        draw = ImageDraw.Draw(img)

        # Capsule
        draw.rounded_rectangle([cx-76, 176, cx+76, 432], radius=76, fill=w)
        # U mount
        draw.arc([cx-136, 352, cx+136, 544], 0, 180, fill=w, width=24)
        # Stem
        draw.line([cx, 544, cx, 600], fill=w, width=24)
        # Base
        draw.rounded_rectangle([cx-68, 588, cx+68, 616], radius=14, fill=w)

        # Resize to render size (200x200 in a 320x320 frame)
        target = int(self.MIC_DISPLAY * self.RENDER_SIZE / self.DISPLAY_SIZE)
        return img.resize((target, target), Image.LANCZOS)

    def _generate_noise_texture(self, size, seed):
        """Multi-octave noise texture (size x size) for pixel displacement.

        Generates a smooth turbulence-like noise field with 5 octaves.
        Uses only numpy + PIL (no external noise libraries).
        """
        rng = np.random.default_rng(seed)
        result = np.zeros((size, size), dtype=np.float32)
        for octave in range(5):
            grid_size = 4 * (2 ** octave)  # 4, 8, 16, 32, 64
            if grid_size >= size:
                break
            amp = 1.0 / (1 + octave * 0.7)
            grid = rng.uniform(-1, 1, (grid_size, grid_size)).astype(np.float32)
            grid_img = Image.fromarray(((grid + 1) * 127.5).astype(np.uint8), mode='L')
            smooth = np.array(grid_img.resize((size, size), Image.BICUBIC)).astype(np.float32)
            result += amp * (smooth / 127.5 - 1.0)
        max_val = np.abs(result).max()
        if max_val > 0:
            result /= max_val
        return result

    def _apply_displacement(self, img_arr, dx, dy):
        """2D pixel displacement with bilinear interpolation (pure numpy).

        Shifts each image pixel independently based on dx/dy fields.
        This simulates SVG feDisplacementMap: the ring is warped per pixel,
        not moved as one piece (as in the polyline approach).
        """
        h, w = img_arr.shape[:2]
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)

        src_x = np.clip(xs + dx, 0, w - 1)
        src_y = np.clip(ys + dy, 0, h - 1)

        x0 = np.floor(src_x).astype(int)
        y0 = np.floor(src_y).astype(int)
        x1 = np.minimum(x0 + 1, w - 1)
        y1 = np.minimum(y0 + 1, h - 1)

        fx = (src_x - x0)[:, :, np.newaxis]
        fy = (src_y - y0)[:, :, np.newaxis]

        result = (
            img_arr[y0, x0] * (1 - fx) * (1 - fy) +
            img_arr[y1, x0] * (1 - fx) * fy +
            img_arr[y0, x1] * fx * (1 - fy) +
            img_arr[y1, x1] * fx * fy
        )
        return np.clip(result, 0, 255).astype(np.uint8)

    def _make_display_frame(self, frame_rgba):
        """Convert RGBA frame (RENDER_SIZE) to RGB (DISPLAY_SIZE) with transparency.

        Composite against dark red instead of black/(1,1,1), so semi-transparent
        glow edge pixels appear dark red instead of near-black.
        """
        img = frame_rgba.resize((self.DISPLAY_SIZE, self.DISPLAY_SIZE), Image.LANCZOS)
        tc = self.TRANS_COLOR
        # Composite against dark red (semi-transparent pixels -> dark red, not black)
        comp_bg = Image.new("RGBA", (self.DISPLAY_SIZE, self.DISPLAY_SIZE), (80, 15, 15, 255))
        composited = Image.alpha_composite(comp_bg, img)
        alpha = img.split()[3]
        mask = alpha.point(lambda x: 255 if x > 6 else 0)
        rgb = Image.new("RGB", (self.DISPLAY_SIZE, self.DISPLAY_SIZE), tc)
        rgb.paste(composited.convert("RGB"), mask=mask)
        return rgb

    def _prerender_frames(self):
        """Pre-render Electric Border frames (optimized).

        All blur layers are merged into 2 composite images BEFORE the loop.
        Per frame: only 2 displacement ops (instead of 6) and 0 blur ops (instead of 6+).
        """
        t0 = time.time()
        rs = self.RENDER_SIZE  # 320
        cx = rs // 2  # 160

        mic_r = int(self.MIC_DISPLAY / 2 * rs / self.DISPLAY_SIZE)
        border_r = mic_r + 1   # Ring sits directly on mic edge (almost no gap)
        outer_r = border_r + 18

        # --- Fill disc: red background under all rings ---
        # Covers the full area behind rings, even at max displacement.
        # Color matched to mic outer gradient (205, 45, 45).
        fill_disc = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        fill_r = outer_r + 20  # Generous radius extending well below outer ring
        ImageDraw.Draw(fill_disc).ellipse(
            [cx - fill_r, cx - fill_r, cx + fill_r, cx + fill_r],
            fill=(200, 42, 42, 255))
        fill_disc = fill_disc.filter(ImageFilter.GaussianBlur(radius=8))

        # --- Draw ring layers ---
        ring_core = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        ImageDraw.Draw(ring_core).ellipse(
            [cx - border_r, cx - border_r, cx + border_r, cx + border_r],
            outline=(255, 235, 220, 245), width=3)

        ring_sharp = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        ImageDraw.Draw(ring_sharp).ellipse(
            [cx - border_r, cx - border_r, cx + border_r, cx + border_r],
            outline=(255, 120, 80, 220), width=5)

        ring_glow = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        ImageDraw.Draw(ring_glow).ellipse(
            [cx - border_r, cx - border_r, cx + border_r, cx + border_r],
            outline=(239, 68, 68, 200), width=14)

        ring_ambient = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        ImageDraw.Draw(ring_ambient).ellipse(
            [cx - border_r, cx - border_r, cx + border_r, cx + border_r],
            outline=(239, 50, 50, 160), width=22)

        ring_outer = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        ImageDraw.Draw(ring_outer).ellipse(
            [cx - outer_r, cx - outer_r, cx + outer_r, cx + outer_r],
            outline=(255, 100, 80, 140), width=2)

        ring_outer_glow = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        ImageDraw.Draw(ring_outer_glow).ellipse(
            [cx - outer_r, cx - outer_r, cx + outer_r, cx + outer_r],
            outline=(239, 60, 60, 120), width=8)

        # --- Pre-composite: merge all layers BEFORE loop and blur ---
        # Inner composite (6 layers -> 1 image, only 1 displacement per frame)
        inner = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        inner = Image.alpha_composite(inner, ring_ambient.filter(ImageFilter.GaussianBlur(radius=20)))
        inner = Image.alpha_composite(inner, ring_glow.filter(ImageFilter.GaussianBlur(radius=10)))
        inner = Image.alpha_composite(inner, ring_glow.filter(ImageFilter.GaussianBlur(radius=4)))
        inner = Image.alpha_composite(inner, ring_glow.filter(ImageFilter.GaussianBlur(radius=2)))
        inner = Image.alpha_composite(inner, ring_sharp)
        inner = Image.alpha_composite(inner, ring_core)
        inner_arr = np.array(inner).astype(np.float32)

        # Outer composite (2 layers -> 1 image, only 1 displacement per frame)
        outer = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        outer = Image.alpha_composite(outer, ring_outer_glow.filter(ImageFilter.GaussianBlur(radius=8)))
        outer = Image.alpha_composite(outer, ring_outer)
        outer_arr = np.array(outer).astype(np.float32)

        # --- Noise textures ---
        pad = 100
        tex_size = rs + pad * 2  # 520
        noise_tex_x = self._generate_noise_texture(tex_size, seed=42)
        noise_tex_y = self._generate_noise_texture(tex_size, seed=137)
        noise_tex_x2 = self._generate_noise_texture(tex_size, seed=73)
        noise_tex_y2 = self._generate_noise_texture(tex_size, seed=211)

        disp_scale = 15.0
        disp_scale_outer = 10.0
        pan_radius = 60

        frames = []

        for fi in range(self.NUM_FRAMES):
            t = fi / self.NUM_FRAMES * 2 * math.pi

            # Breathing pulse (2 pulses per 3s loop)
            breath = 0.85 + 0.15 * math.sin(t * 2)

            # Core flash: 3 short flashes per loop
            flash = 0.0
            for flash_phase in [1.05, 3.14, 5.24]:
                dist = abs(t - flash_phase)
                if dist > math.pi:
                    dist = 2 * math.pi - dist
                if dist < 0.3:
                    flash = max(flash, 1.0 - dist / 0.3)

            # --- Inner displacement ---
            ox = int(pad + pan_radius * math.cos(t))
            oy = int(pad + pan_radius * math.sin(t))
            dx = noise_tex_x[oy:oy+rs, ox:ox+rs] * disp_scale

            ox2 = int(pad + pan_radius * math.cos(2*t + 1.5))
            oy2 = int(pad + pan_radius * math.sin(t + 0.8))
            dy = noise_tex_y[oy2:oy2+rs, ox2:ox2+rs] * disp_scale

            # --- Outer displacement ---
            oxa = int(pad + pan_radius * 0.7 * math.cos(t * 0.7 + 2.0))
            oya = int(pad + pan_radius * 0.7 * math.sin(t * 0.7))
            dx_out = noise_tex_x2[oya:oya+rs, oxa:oxa+rs] * disp_scale_outer

            oxb = int(pad + pan_radius * 0.7 * math.cos(t * 1.3 + 1.0))
            oyb = int(pad + pan_radius * 0.7 * math.sin(t * 0.9 + 0.5))
            dy_out = noise_tex_y2[oyb:oyb+rs, oxb:oxb+rs] * disp_scale_outer

            # 2 displacements (instead of 6)
            disp_inner = self._apply_displacement(inner_arr, dx, dy)
            disp_outer = self._apply_displacement(outer_arr, dx_out, dy_out)

            # Breathing: modulate inner composite alpha
            if breath < 1.0:
                disp_inner[:, :, 3] = (disp_inner[:, :, 3].astype(np.float32) * breath).astype(np.uint8)

            # Compose frame: fill disc -> electric rings -> mic
            frame = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
            frame = Image.alpha_composite(frame, fill_disc)  # Fill the gap
            frame = Image.alpha_composite(frame, Image.fromarray(disp_outer, mode="RGBA"))
            frame = Image.alpha_composite(frame, Image.fromarray(disp_inner, mode="RGBA"))

            # Core flash: briefly boost brightness
            if flash > 0:
                frame_arr = np.array(frame)
                boost = 1.0 + flash * 0.35
                frame_arr[:, :, :3] = np.minimum(255,
                    (frame_arr[:, :, :3].astype(np.float32) * boost)).astype(np.uint8)
                frame = Image.fromarray(frame_arr, mode="RGBA")

            # Mic icon (not displaced, stays sharp)
            if self._mic_rgba:
                mic_rs = self._mic_rgba.size[0]
                offset = cx - mic_rs // 2
                frame.paste(self._mic_rgba, (offset, offset), self._mic_rgba)

            frames.append(self._make_display_frame(frame))

        self._frames = frames
        self._frames_ready = True
        duration = time.time() - t0
        append_to_history(
            f"[STARTUP] Electric Border pre-rendered: {self.NUM_FRAMES} frames in {duration:.1f}s")

    def start(self):
        """Start overlay in a dedicated thread."""
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self):
        import tkinter as tk
        from PIL import ImageTk

        self.root = tk.Tk()
        self.root.withdraw()

        # Pre-render mic icon
        self._mic_rgba = self._create_mic_icon()
        self._t0 = time.time()

        # Static fallback frame (mic icon with fill disc, no Electric Border)
        rs = self.RENDER_SIZE
        cx = rs // 2
        mic_r = int(self.MIC_DISPLAY / 2 * rs / self.DISPLAY_SIZE)
        fill_r = mic_r + 30
        static = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        ImageDraw.Draw(static).ellipse(
            [cx - fill_r, cx - fill_r, cx + fill_r, cx + fill_r],
            fill=(185, 38, 38, 255))
        static = static.filter(ImageFilter.GaussianBlur(radius=6))
        if self._mic_rgba:
            mic_rs = self._mic_rgba.size[0]
            offset = cx - mic_rs // 2
            static.paste(self._mic_rgba, (offset, offset), self._mic_rgba)
        self._static_frame = self._make_display_frame(static)

        # Start pre-rendering in separate thread (runs in parallel with model load)
        prerender_thread = threading.Thread(target=self._prerender_frames, daemon=True)
        prerender_thread.start()

        monitors = get_monitors()

        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x20
        WS_EX_LAYERED = 0x80000

        # --- Red bars on all monitors ---
        for i, (left, top, right, bottom) in enumerate(monitors):
            win = tk.Toplevel(self.root)
            title = f"WhisperREC_{i}"
            win.title(title)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg="#ef4444")

            width = right - left
            win.geometry(f"{width}x{self.BAR_HEIGHT}+{left}+{top}")

            # Click-through behavior
            win.update_idletasks()
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_TRANSPARENT)

            win.withdraw()
            self._windows.append(win)

        # --- Electric Border window (160x160, top-left) ---
        orb_win = tk.Toplevel(self.root)
        orb_title = "WhisperORB"
        orb_win.title(orb_title)
        orb_win.overrideredirect(True)
        orb_win.attributes("-topmost", True)
        trans = "#{:02x}{:02x}{:02x}".format(*self.TRANS_COLOR)
        orb_win.configure(bg=trans)
        orb_win.attributes("-transparentcolor", trans)

        primary = monitors[0] if monitors else (0, 0, 1920, 1080)
        orb_x = primary[0] + 12
        orb_y = primary[1] + self.BAR_HEIGHT + 8
        orb_win.geometry(f"{self.DISPLAY_SIZE}x{self.DISPLAY_SIZE}+{orb_x}+{orb_y}")

        # Initial empty frame
        init_img = Image.new("RGB", (self.DISPLAY_SIZE, self.DISPLAY_SIZE), self.TRANS_COLOR)
        self._orb_photo = ImageTk.PhotoImage(init_img)
        self._orb_label = tk.Label(orb_win, image=self._orb_photo, bg=trans, bd=0,
                                   highlightthickness=0)
        self._orb_label.pack()

        orb_win.update_idletasks()
        hwnd_orb = user32.FindWindowW(None, orb_title)
        if hwnd_orb:
            ex_style = user32.GetWindowLongW(hwnd_orb, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd_orb, GWL_EXSTYLE,
                                  ex_style | WS_EX_TRANSPARENT | WS_EX_LAYERED)

        orb_win.withdraw()
        self._orb_win = orb_win

        # Poll recording status every 100ms
        self._poll()
        self.root.mainloop()

    def _poll(self):
        """Show/hide overlay based on recording status."""
        if recording and not self._visible:
            for win in self._windows:
                win.deiconify()
            if self._orb_win:
                self._orb_win.deiconify()
            self._visible = True
            self._animate()
        elif not recording and self._visible:
            for win in self._windows:
                win.withdraw()
            if self._orb_win:
                self._orb_win.withdraw()
            self._visible = False

        # Dashboard toggle (from tray left click)
        if _dashboard_toggle.is_set():
            _dashboard_toggle.clear()
            self._toggle_dashboard()

        # Auto-close dashboard when recording starts
        if recording and self._dashboard_visible:
            self._destroy_dashboard()

        self.root.after(100, self._poll)

    def _animate(self):
        """Animation: cycle pre-rendered frames + bar pulse at ~30 FPS."""
        if not self._visible:
            return

        from PIL import ImageTk

        # Select frame: Calm Mode = always static, otherwise Electric Border
        if calm_mode or not self._frames_ready:
            frame = self._static_frame
        else:
            elapsed = time.time() - self._t0
            idx = int(elapsed * 30) % self.NUM_FRAMES
            frame = self._frames[idx]

        self._orb_photo = ImageTk.PhotoImage(frame)
        self._orb_label.configure(image=self._orb_photo)

        # Bar pulse (subtle, ~3s cycle)
        phase = time.time() * 2.0 * math.pi / 3.0
        factor = (math.sin(phase) + 1) / 2
        r = int(185 + factor * 54)
        g = int(28 + factor * 40)
        b = int(28 + factor * 40)
        color = f"#{r:02x}{g:02x}{b:02x}"
        for win in self._windows:
            win.configure(bg=color)

        self.root.after(33, self._animate)  # ~30 FPS

    # ================================================================
    # Dashboard popup
    # ================================================================

    def _toggle_dashboard(self):
        """Toggle dashboard visibility."""
        if self._dashboard_visible:
            self._destroy_dashboard()
        else:
            self._create_dashboard()

    def _destroy_dashboard(self):
        """Close dashboard and clean up."""
        if self._dashboard_win:
            try:
                self._dashboard_win.destroy()
            except Exception:
                pass
            self._dashboard_win = None
        self._dashboard_visible = False

    def _create_dashboard(self):
        """Premium dashboard popup with stats, history, and controls."""
        import tkinter as tk

        if self._dashboard_win:
            self._destroy_dashboard()
            return

        # Design tokens
        BG = "#0c0c18"
        CARD = "#13132a"
        CARD_BORDER = "#222244"
        BORDER = "#282850"
        ACCENT = "#ef4444"
        GREEN = "#22c55e"
        AMBER = "#f59e0b"
        TEXT = "#eaeaf2"
        TEXT2 = "#9494b0"
        TEXT3 = "#5c5c78"
        DIVIDER = "#1c1c38"
        BTN = "#181834"
        BTN_HOVER = "#262650"
        CALM_ON_BG = "#14301a"
        CALM_ON_HOVER = "#1e4826"
        LOG_ROW_HOVER = "#12122a"
        WIDTH = 370

        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=BORDER)

        # Outer frame (1px border)
        inner = tk.Frame(win, bg=BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        # Red accent line on top
        tk.Frame(inner, bg=ACCENT, height=2).pack(fill="x")

        # Main area
        main = tk.Frame(inner, bg=BG)
        main.pack(fill="both", expand=True, padx=24, pady=(20, 20))

        # Enforce minimum width
        spacer = tk.Frame(main, bg=BG, height=0, width=WIDTH - 50)
        spacer.pack(fill="x")
        spacer.pack_propagate(False)

        # Header
        hdr = tk.Frame(main, bg=BG)
        hdr.pack(fill="x", pady=(0, 4))

        tk.Label(hdr, text="Whisper Diktiertool",
                 font=("Segoe UI Semibold", 14), fg=TEXT, bg=BG).pack(side="left")

        close_btn = tk.Label(hdr, text="\u2715", font=("Segoe UI", 12),
                             fg=TEXT3, bg=BG, cursor="hand2", padx=4)
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self._destroy_dashboard())
        close_btn.bind("<Enter>", lambda e: close_btn.configure(fg=ACCENT))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(fg=TEXT3))

        # Status row
        status_f = tk.Frame(main, bg=BG)
        status_f.pack(fill="x", pady=(2, 16))

        dot_cv = tk.Canvas(status_f, width=10, height=10, bg=BG, highlightthickness=0)
        dot_cv.pack(side="left", padx=(0, 8), pady=3)

        if recording:
            dot_color, status_text = ACCENT, "Recording..."
        elif model is not None:
            dot_color, status_text = GREEN, "Ready"
        else:
            dot_color, status_text = "#71717a", "Loading model..."

        dot_cv.create_oval(1, 1, 9, 9, fill=dot_color, outline=dot_color)

        tk.Label(status_f, text=status_text, font=("Segoe UI", 10),
                 fg=TEXT2, bg=BG).pack(side="left")

        tk.Label(status_f, text="CTRL+ALT+D", font=("Consolas", 9),
                 fg=TEXT3, bg=BG).pack(side="right")

        # Divider
        tk.Frame(main, bg=DIVIDER, height=1).pack(fill="x", pady=(0, 16))

        # Stats cards
        count, total_sec = get_today_stats()
        minutes = total_sec / 60

        tk.Label(main, text="TODAY", font=("Segoe UI Semibold", 9),
                 fg=TEXT3, bg=BG).pack(anchor="w", pady=(0, 10))

        cards_row = tk.Frame(main, bg=BG)
        cards_row.pack(fill="x", pady=(0, 16))

        def make_stat_card(parent, value, label, accent_color, pad_kw):
            wrapper = tk.Frame(parent, bg=CARD_BORDER)
            wrapper.pack(side="left", expand=True, fill="x", **pad_kw)

            card_inner = tk.Frame(wrapper, bg=CARD)
            card_inner.pack(fill="both", expand=True, padx=1, pady=1)

            # Colored accent bar on the left
            accent_bar = tk.Frame(card_inner, bg=accent_color, width=3)
            accent_bar.pack(side="left", fill="y")
            accent_bar.pack_propagate(False)

            content = tk.Frame(card_inner, bg=CARD, padx=14, pady=10)
            content.pack(side="left", fill="both", expand=True)

            tk.Label(content, text=str(value), font=("Segoe UI", 26, "bold"),
                     fg=TEXT, bg=CARD).pack(anchor="w")
            tk.Label(content, text=label, font=("Segoe UI", 9),
                     fg=TEXT2, bg=CARD).pack(anchor="w", pady=(2, 0))

        make_stat_card(cards_row, count, "Dictations", GREEN, {"padx": (0, 5)})
        make_stat_card(cards_row, f"{minutes:.1f}", "Min Saved", AMBER, {"padx": (5, 0)})

        # Divider
        tk.Frame(main, bg=DIVIDER, height=1).pack(fill="x", pady=(0, 16))

        # History
        logs = get_recent_logs(8)

        history_hdr = tk.Frame(main, bg=BG)
        history_hdr.pack(fill="x", pady=(0, 10))

        tk.Label(history_hdr, text="HISTORY", font=("Segoe UI Semibold", 9),
                 fg=TEXT3, bg=BG).pack(side="left")

        tk.Label(history_hdr, text="click to copy", font=("Segoe UI", 8),
                 fg=TEXT3, bg=BG).pack(side="right")

        if logs:
            from datetime import datetime
            today_str = datetime.now().strftime("%Y-%m-%d")

            COPIED_BG = "#1a2a1a"  # Short green flash after copy

            for entry in reversed(logs):
                row = tk.Frame(main, bg=BG, cursor="hand2")
                row.pack(fill="x", pady=1)

                # Date prefix for older entries
                if entry["date"] == today_str:
                    time_display = entry["time"][:5]
                    time_width = 5
                else:
                    time_display = entry["date"][5:] + " " + entry["time"][:5]
                    time_width = 11

                tk.Label(row, text=time_display, font=("Consolas", 9),
                         fg=TEXT3, bg=BG, width=time_width, anchor="w").pack(side="left")

                if entry["duration"] > 0:
                    dur = f"{entry['duration']:.0f}s"
                    tk.Label(row, text=dur, font=("Consolas", 9),
                             fg=ACCENT, bg=BG, width=4, anchor="e").pack(side="left", padx=(6, 8))
                else:
                    tk.Frame(row, bg=BG, width=52).pack(side="left")

                # Text preview (trimmed)
                preview = entry["text"]
                max_chars = 30 if entry["date"] != today_str else 36
                if len(preview) > max_chars:
                    preview = preview[:max_chars] + "\u2026"
                tk.Label(row, text=preview, font=("Segoe UI", 9),
                         fg=TEXT2, bg=BG, anchor="w").pack(side="left", fill="x")

                # Click copies full text to clipboard
                full_text = entry["text"]
                all_widgets = [row] + list(row.winfo_children())

                def copy_text(e, txt=full_text, ws=all_widgets):
                    pyperclip.copy(txt)
                    # Green flash as confirmation
                    for w in ws:
                        w.configure(bg=COPIED_BG)
                    row_ref = ws[0]
                    row_ref.after(400, lambda: [
                        w.configure(bg=BG) for w in ws if w.winfo_exists()])

                for w in all_widgets:
                    w.bind("<Button-1>", copy_text)
                    w.bind("<Enter>", lambda e, ws=all_widgets: [
                        x.configure(bg=LOG_ROW_HOVER) for x in ws])
                    w.bind("<Leave>", lambda e, ws=all_widgets: [
                        x.configure(bg=BG) for x in ws])
        else:
            tk.Label(main, text="No dictations yet",
                     font=("Segoe UI", 9), fg=TEXT3, bg=BG).pack(anchor="w", pady=(0, 4))

        # Divider
        tk.Frame(main, bg=DIVIDER, height=1).pack(fill="x", pady=(14, 16))

        # Action buttons
        btns = tk.Frame(main, bg=BG)
        btns.pack(fill="x")

        def make_action_btn(parent, text, command, bg_c=BTN, hover_c=BTN_HOVER, fg_c=TEXT):
            btn = tk.Label(parent, text=text, font=("Segoe UI Semibold", 9),
                           fg=fg_c, bg=bg_c, padx=12, pady=7, cursor="hand2")
            btn.pack(side="left", padx=(0, 6))
            btn.bind("<Button-1>", lambda e: command())
            btn.bind("<Enter>", lambda e: btn.configure(bg=hover_c))
            btn.bind("<Leave>", lambda e: btn.configure(bg=bg_c))
            return btn

        if calm_mode:
            make_action_btn(btns, "\u2713 Calm Mode", self._dash_toggle_calm,
                            CALM_ON_BG, CALM_ON_HOVER)
        else:
            make_action_btn(btns, "Calm Mode", self._dash_toggle_calm)

        make_action_btn(btns, "\u21bb Restart", self._dash_restart)
        make_action_btn(btns, "\u23fb Quit", self._dash_quit)

        # Positioning and animation
        win.update_idletasks()
        win_w = max(WIDTH, win.winfo_reqwidth())
        win_h = win.winfo_reqheight()

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        x = screen_w - win_w - 16
        y_end = screen_h - win_h - 52
        y_start = y_end + 20

        win.geometry(f"{win_w}x{win_h}+{x}+{y_start}")
        win.attributes("-alpha", 0.0)

        self._dashboard_win = win
        self._dashboard_visible = True

        # Slide-up and fade-in
        self._dash_animate(x, win_w, win_h, y_end, y_start, 0)

    def _dash_animate(self, x, w, h, y_end, y_start, step):
        """Ease-out slide-up and fade-in animation."""
        if not self._dashboard_win:
            return
        total = 8
        if step > total:
            return

        t = step / total
        ease = 1 - (1 - t) ** 3  # Ease-out cubic

        y = int(y_start + (y_end - y_start) * ease)
        alpha = min(1.0, ease * 1.3)

        try:
            self._dashboard_win.geometry(f"{w}x{h}+{x}+{y}")
            self._dashboard_win.attributes("-alpha", alpha)
        except Exception:
            return

        if step < total:
            self.root.after(18, self._dash_animate, x, w, h, y_end, y_start, step + 1)

    def _dash_toggle_calm(self):
        """Toggle Calm Mode and rebuild dashboard."""
        global calm_mode
        calm_mode = not calm_mode
        save_config()
        self._destroy_dashboard()
        self.root.after(50, self._create_dashboard)

    def _dash_restart(self):
        """Restart from dashboard."""
        self._destroy_dashboard()
        if tray_icon:
            on_restart(tray_icon, None)

    def _dash_quit(self):
        """Quit from dashboard."""
        self._destroy_dashboard()
        if tray_icon:
            on_quit(tray_icon, None)


def load_model():
    """Load Whisper model at startup."""
    global model
    import traceback
    try:
        from faster_whisper import WhisperModel

        t0 = time.time()
        model = WhisperModel(
            MODEL_SIZE,
            device="cuda",
            compute_type="int8_float16",
        )
        load_time = time.time() - t0
        append_to_history(f"[STARTUP] Model loaded in {load_time:.1f}s")
        update_tray("Bereit (CTRL+ALT+D)", create_icon_idle())
        play_ready_sound()
    except Exception:
        # Write error to log file (pythonw has no console)
        log_path = os.path.join(os.path.dirname(__file__), "whisper-error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        update_tray("FEHLER - siehe whisper-error.log", create_icon_loading())


def audio_callback(indata, frames, time_info, status):
    """Called during recording."""
    global audio_overflow_count, audio_level
    if status:
        # Input overflow = audio data was lost (buffer too small)
        audio_overflow_count += 1
    if recording:
        audio_chunks.append(indata.copy())
        # Compute RMS level for orb animation (0.0-1.0)
        audio_level = min(1.0, np.sqrt(np.mean(indata**2)) * 5.0)


def start_recording():
    """Start recording."""
    global recording, audio_chunks, audio_overflow_count, stream, target_window
    if recording:
        return

    target_window = user32.GetForegroundWindow()

    # Play sound BEFORE recording (so beep is not recorded)
    play_start_sound()

    audio_chunks = []
    audio_overflow_count = 0
    recording = True
    stream = sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="float32",
        callback=audio_callback,
        blocksize=1024,
        latency="high",
    )
    stream.start()

    update_tray("Aufnahme...", create_icon_recording())


def stop_recording_and_transcribe():
    """Stop recording, transcribe, and insert text."""
    global recording, stream, audio_level
    if not recording:
        return

    recording = False
    audio_level = 0.0

    if stream:
        stream.stop()
        stream.close()
        stream = None

    # Play sound AFTER stopping (recording already ended)
    play_stop_sound()

    update_tray("Transkribiere...", create_icon_loading())

    if not audio_chunks:
        update_tray("Bereit (CTRL+ALT+D)", create_icon_idle())
        return

    chunk_count = len(audio_chunks)
    audio = np.concatenate(audio_chunks, axis=0).flatten()
    duration = len(audio) / SAMPLE_RATE

    # Write overflow warning to log
    if audio_overflow_count > 0:
        try:
            from datetime import datetime
            log_path = os.path.join(os.path.dirname(__file__), "whisper-history.log")
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{timestamp}] ⚠ OVERFLOW: {audio_overflow_count}x Input-Overflow, "
                        f"{chunk_count} Chunks, {duration:.1f}s Audio\n")
        except Exception:
            pass

    if duration < 0.3:
        update_tray("Bereit (CTRL+ALT+D)", create_icon_idle())
        return

    try:
        t_start = time.time()
        segments, info = model.transcribe(
            audio,
            language="de",
            beam_size=3,
            vad_filter=True,
            condition_on_previous_text=False,
            initial_prompt=INITIAL_PROMPT,
        )

        # Fully consume generator (prevents data loss on iteration errors)
        segments_list = list(segments)
        t_transcribe = time.time() - t_start

        parts = filter_hallucinations(segments_list)
        text = " ".join(parts).strip()
        text = apply_spoken_punctuation(text)
        text = apply_word_corrections(text)
        text = remove_trailing_period(text)

        # Performance log
        ratio = duration / t_transcribe if t_transcribe > 0 else 0
        append_to_history(f"[PERF] {duration:.1f}s Audio → {t_transcribe:.1f}s Transkription ({ratio:.1f}x Echtzeit)")

        if text:
            if target_window:
                user32.SetForegroundWindow(target_window)
                time.sleep(0.1)

            old_clipboard = ""
            try:
                old_clipboard = pyperclip.paste()
            except Exception:
                pass

            pyperclip.copy(text)
            time.sleep(0.05)
            keyboard.send("ctrl+v")

            time.sleep(0.15)
            try:
                pyperclip.copy(old_clipboard)
            except Exception:
                pass

            append_to_history(text, duration)

    except Exception as e:
        append_to_history(f"[FEHLER] Transkription fehlgeschlagen: {e}")

    update_tray("Bereit (CTRL+ALT+D)", create_icon_idle())


def hotkey_loop():
    """Keyboard loop in dedicated thread."""
    # Wait until model is loaded
    while model is None:
        time.sleep(0.1)

    while True:
        keyboard.wait(HOTKEY)
        if not recording:
            start_recording()
        else:
            stop_recording_and_transcribe()
        while keyboard.is_pressed(HOTKEY):
            time.sleep(0.01)


def on_restart(icon, item):
    """Restart via tray menu: launch new instance, then exit current one.

    Uses pythonw (not cmd.exe) for delayed start,
    so no terminal window is visible.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, "whisper-dictate.py")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    # pythonw -c for delay: fully hidden, no cmd.exe needed
    restart_code = (
        "import time,subprocess;"
        f"time.sleep(2);"
        f"subprocess.Popen([r'{pythonw}',r'{script_path}'],cwd=r'{script_dir}')"
    )
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(
        [pythonw, "-c", restart_code],
        creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW,
        close_fds=True,
    )
    icon.stop()
    os._exit(0)


def on_toggle_calm(icon, item):
    """Toggle Calm Mode (static mic icon instead of Electric Border)."""
    global calm_mode
    calm_mode = not calm_mode
    save_config()


def on_activate(icon, item):
    """Open/close dashboard on left click of tray icon."""
    _dashboard_toggle.set()


def on_quit(icon, item):
    """Quit via tray menu."""
    icon.stop()
    os._exit(0)


def main():
    global tray_icon

    # Ensure autostart (set Registry Run key, clean old .lnk)
    ensure_autostart()

    # Load config (calm_mode etc.)
    load_config()

    # Create tray icon (menu only for default action, native menu disabled)
    menu = pystray.Menu(
        pystray.MenuItem("Dashboard", on_activate, default=True, visible=False),
    )
    tray_icon = pystray.Icon(
        "whisper-dictate",
        create_icon_loading(),
        "Whisper Diktiertool - Lade Modell...",
        menu,
    )

    # Right click should open dashboard instead of native menu (same as left click)
    _original_on_notify = tray_icon._on_notify
    def _patched_on_notify(wparam, lparam):
        if lparam == 0x0205:  # WM_RBUTTONUP: dashboard instead of popup menu
            tray_icon()
        else:
            _original_on_notify(wparam, lparam)
    tray_icon._on_notify = _patched_on_notify

    # Recording overlay (floating "REC" indicator)
    overlay = RecordingOverlay()
    overlay.start()

    # Run hotkey loop and model loading in background threads
    hotkey_thread = threading.Thread(target=hotkey_loop, daemon=True)
    hotkey_thread.start()

    model_thread = threading.Thread(target=load_model, daemon=True)
    model_thread.start()

    # Tray icon blocks main thread (required)
    tray_icon.run()


if __name__ == "__main__":
    main()
