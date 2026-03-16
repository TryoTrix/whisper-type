"""
Whisper Diktiertool - Sprechen & Text einfuegen
================================================
Druecke CTRL+ALT+D zum Starten/Stoppen der Aufnahme.
Laeuft als System Tray Icon (kein Taskleisten-Eintrag).
Nur eine Instanz laeuft gleichzeitig (Mutex-geschuetzt).

Starten:
    pythonw whisper-dictate.py
"""

import sys
import os
import ctypes
from ctypes import wintypes

# Single-Instance: Windows Mutex verhindert Doppelstart
_mutex = ctypes.windll.kernel32.CreateMutexW(None, True, "WhisperDiktiertool_Mutex")
if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
    sys.exit(0)

# NVIDIA DLLs fuer CUDA sichtbar machen (cublas, cudnn)
_nvidia_base = os.path.join(
    os.path.dirname(sys.executable), "Lib", "site-packages", "nvidia"
)
_dll_dirs = []
for _lib in ("cublas", "cudnn"):
    _dll_dir = os.path.join(_nvidia_base, _lib, "bin")
    if os.path.isdir(_dll_dir):
        os.add_dll_directory(_dll_dir)
        _dll_dirs.append(_dll_dir)
# Auch PATH erweitern (CTranslate2 laedt DLLs ueber LoadLibrary)
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
# KONFIGURATION - Hier anpassen
# ============================================================
HOTKEY = "ctrl+alt+d"
SAMPLE_RATE = 16000      # Whisper erwartet 16kHz
MODEL_SIZE = "large-v3-turbo"  # Schneller (~3-5s statt ~12-16s), gute Qualitaet
NO_SPEECH_THRESHOLD = None  # Deaktiviert (no_speech_prob ist bei Deutsch unzuverlaessig)
DEBUG_TRANSCRIPTION = True   # Segment-Details ins History-Log schreiben
SHORT_TEXT_MAX_WORDS = 3     # Bei <= N Woertern: trailing Punkt entfernen

# Fachbegriffe die Whisper korrekt erkennen soll (biased den Decoder, kein Performance-Impact)
INITIAL_PROMPT = "CLAUDE.md, Whisper, faster-whisper, Python, CUDA, RTX 4060, committe, pushe, Punkt, YOLO, TryoTrix, CMD, committen, pushen, Commit, Push"

# Gesprochene Satzzeichen → echte Zeichen (Regex-Pattern, case-insensitive)
# Kommas/Leerzeichen vor und nach dem Wort werden mit-konsumiert
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

# Wortkorrekturen: Whisper-Fehlerkennungen → richtige Schreibweise (Regex, case-insensitive)
WORD_CORRECTIONS = {
    r'\bTrial[\s-]?Tricks?\b': 'TryoTrix',
    r'\bTry[\s-]?o[\s-]?Tricks?\b': 'TryoTrix',
    r'\bTryo[\s-]?Tricks?\b': 'TryoTrix',
    r'\bTry[\s-]?your[\s-]?Tricks?\b': 'TryoTrix',
    r'\bTriotricks?\b': 'TryoTrix',
    r'\bTryotricks?\b': 'TryoTrix',
}

# Bekannte Whisper-Halluzinationen bei Stille (lowercase fuer Vergleich)
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

# Win32 API fuer Fensterverwaltung
user32 = ctypes.windll.user32

# Globale Variablen
recording = False
audio_chunks = []
audio_overflow_count = 0
audio_level = 0.0  # RMS-Pegel 0.0-1.0, wird in audio_callback aktualisiert
model = None
stream = None
target_window = None
tray_icon = None
calm_mode = False  # True = statisches Mic-Icon statt Electric Border
_dashboard_toggle = threading.Event()  # Signal von Tray (Links-Klick) an tkinter-Thread


def create_icon_idle():
    """Gruenes Icon = bereit."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill="#22c55e")
    return img


def create_icon_recording():
    """Rotes Icon = Aufnahme laeuft."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill="#ef4444")
    return img


def create_icon_loading():
    """Graues Icon = Modell wird geladen."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill="#9ca3af")
    return img


def get_today_stats():
    """Heutige Diktate zaehlen und Audio-Dauer summieren aus whisper-history.log."""
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
                # Nur echte Diktate: [2026-02-23 15:47:45] (14.8s) Text...
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
    """Letzte Diktate aus whisper-history.log fuer Dashboard-Anzeige.

    Filtert DEBUG/PERF/STARTUP-Zeilen heraus, parst nur echte Diktate.
    Liest nur die letzten 500 Zeilen fuer Performance bei grossen Logdateien.
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
            # Neues Format mit Dauer: [2026-02-23 14:32:05] (12.3s) Text...
            m = re.match(r'\[(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})\] \((\d+\.?\d*)s\) (.+)', line)
            if m:
                entries.append({
                    "date": m.group(1), "time": m.group(2),
                    "duration": float(m.group(3)), "text": m.group(4).strip()
                })
                continue
            # Altes Format ohne Dauer: [2026-02-17 23:47:02] Text...
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
    """Tray-Icon und Tooltip aktualisieren."""
    if tray_icon:
        tray_icon.icon = icon_img
        count, total_sec = get_today_stats()
        stats = ""
        if count > 0:
            minutes = total_sec / 60
            stats = f" | Heute: {count}x, {minutes:.1f} Min"
        tray_icon.title = f"Whisper Diktiertool - {status_text}{stats}"


def play_start_sound():
    """Kurzer hoher Ton = Aufnahme gestartet."""
    winsound.Beep(800, 100)


def play_stop_sound():
    """Kurzer tiefer Ton = Aufnahme gestoppt."""
    winsound.Beep(500, 100)


def play_ready_sound():
    """Sanfter Ready-Chime nach Modell-Laden (nur beim Start)."""
    try:
        sr = 44100
        # Zwei aufsteigende Töne: G5 → C6 (sanftes "ding-ding")
        t1 = np.linspace(0, 0.12, int(sr * 0.12), False)
        t2 = np.linspace(0, 0.25, int(sr * 0.25), False)
        note1 = np.sin(2 * np.pi * 784 * t1) * np.exp(-t1 * 12)  # G5, kurz
        note2 = np.sin(2 * np.pi * 1047 * t2) * np.exp(-t2 * 6)  # C6, klingt nach
        gap = np.zeros(int(sr * 0.04))  # 40ms Pause
        chime = np.concatenate([note1, gap, note2]) * 0.15  # Leise
        sd.play(chime.astype(np.float32), sr)
    except Exception:
        pass  # Sound ist nice-to-have, kein Crash bei Problemen


def filter_hallucinations(segments):
    """Whisper-Halluzinationen filtern (Stille-Phantome und bekannte Phrasen)."""
    filtered = []
    debug_lines = []
    for seg in segments:
        text = seg.text.strip()
        no_speech = getattr(seg, "no_speech_prob", 0.0)
        # no_speech_prob Filterung deaktiviert: Whisper gibt bei Deutsch oft 0.97
        # fuer klar gesprochene Saetze aus. vad_filter=True macht bereits Audio-VAD.
        if NO_SPEECH_THRESHOLD is not None and no_speech > NO_SPEECH_THRESHOLD:
            if DEBUG_TRANSCRIPTION:
                debug_lines.append(f"  SKIP (no_speech={no_speech:.2f}): {text}")
            continue
        if not text:
            continue
        # Bekannte Halluzinationen pruefen
        text_lower = text.lower().rstrip(".!?,;:")
        if text_lower in HALLUCINATION_PHRASES:
            if DEBUG_TRANSCRIPTION:
                debug_lines.append(f"  SKIP (hallucination): {text}")
            continue
        if DEBUG_TRANSCRIPTION:
            debug_lines.append(f"  KEEP (no_speech={no_speech:.2f}): {text}")
        filtered.append(text)
    # Debug-Info ins Log schreiben
    if DEBUG_TRANSCRIPTION and debug_lines:
        append_to_history("[DEBUG] Segmente:\n" + "\n".join(debug_lines))
    return filtered


def apply_spoken_punctuation(text):
    """Gesprochene Satzzeichen durch echte Zeichen ersetzen."""
    for pattern, replacement in SPOKEN_PUNCTUATION.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = re.sub(r'  +', ' ', text)  # Doppelte Leerzeichen bereinigen
    return text.strip()


def apply_word_corrections(text):
    """Whisper-Fehlerkennungen durch korrekte Schreibweise ersetzen."""
    for pattern, replacement in WORD_CORRECTIONS.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def remove_trailing_period(text):
    """Trailing Punkt entfernen bei kurzen Texten (1-3 Woerter)."""
    if len(text.split()) <= SHORT_TEXT_MAX_WORDS and text.endswith('.'):
        return text[:-1]
    return text


def append_to_history(text, duration=0):
    """Transkription mit Timestamp und Dauer in whisper-history.log speichern."""
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
    """Config aus whisper-config.json laden. Gibt Defaults zurueck falls nicht vorhanden."""
    global calm_mode
    config_path = os.path.join(os.path.dirname(__file__), "whisper-config.json")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        calm_mode = config.get("calm_mode", False)
    except Exception:
        pass


def save_config():
    """Aktuelle Config in whisper-config.json speichern."""
    config_path = os.path.join(os.path.dirname(__file__), "whisper-config.json")
    try:
        config = {"calm_mode": calm_mode}
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)
    except Exception:
        pass


def ensure_autostart():
    """Registry Run-Key erstellen/aktualisieren + alte .lnk aufraeumen."""
    import winreg
    try:
        # Pfade dynamisch ermitteln
        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        script = os.path.abspath(__file__)
        expected_value = f'"{pythonw}" "{script}"'

        # Registry Run-Key pruefen und ggf. setzen
        reg_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
        reg_name = "WhisperDiktiertool"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key, 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                current_value, _ = winreg.QueryValueEx(key, reg_name)
                if current_value == expected_value:
                    # Bereits korrekt, nur Cleanup pruefen
                    _cleanup_old_autostart()
                    return
            except FileNotFoundError:
                pass  # Eintrag existiert noch nicht
            winreg.SetValueEx(key, reg_name, 0, winreg.REG_SZ, expected_value)

        # Alte .lnk und StartupApproved aufraeumen
        _cleanup_old_autostart()
    except Exception:
        pass


def _cleanup_old_autostart():
    """Alte Startup-Verknuepfung und StartupApproved-Geistereintrag entfernen."""
    import winreg
    # Alte .lnk loeschen
    try:
        startup_dir = os.path.join(os.environ["APPDATA"],
                                   "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
        lnk_path = os.path.join(startup_dir, "Whisper Diktiertool.lnk")
        if os.path.exists(lnk_path):
            os.remove(lnk_path)
    except Exception:
        pass
    # StartupApproved-Geistereintrag entfernen (verhindert toten Eintrag im Task Manager)
    try:
        approved_key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, approved_key, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, "Whisper Diktiertool.lnk")
    except Exception:
        pass


def get_monitors():
    """Alle angeschlossenen Monitore ermitteln (Position und Groesse)."""
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
    """Mikrofon-Icon mit Electric Border Effect (pre-gerendert) + roter Balken."""

    BAR_HEIGHT = 8
    DISPLAY_SIZE = 160   # Fenster-Groesse (Mic 100px + Platz fuer Electric Border + Glow)
    RENDER_SIZE = 320    # 2x Supersampling
    MIC_DISPLAY = 100    # Mic-Icon Anzeige-Groesse
    TRANS_COLOR = (1, 1, 1)  # RGB fuer Transparenz
    NUM_FRAMES = 90      # 3s Animation-Loop bei 30fps

    def __init__(self):
        self.root = None
        self._windows = []
        self._orb_win = None
        self._orb_label = None
        self._orb_photo = None
        self._mic_rgba = None       # Vorgerendertes Mic-Icon (RGBA, Render-Groesse)
        self._frames = None         # Liste von pre-gerenderten RGB Frames
        self._frames_ready = False  # True wenn Pre-Rendering abgeschlossen
        self._static_frame = None   # Fallback-Frame (nur Mic, keine Electric Border)
        self._visible = False
        self._t0 = 0
        self._dashboard_win = None
        self._dashboard_visible = False

    def _create_mic_icon(self):
        """Original Mikrofon-Icon als RGBA, 8x Supersampling fuer glatte Raender.

        Rendert bei 800x800 und skaliert auf Render-Groesse (200x200).
        Identisches Design wie das bisherige Icon (roter Gradient-Kreis, weisses Mic).
        """
        hs = 800
        img = Image.new("RGBA", (hs, hs), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        cx = 400

        # --- Kreis: Smooth Gradient (viele Stufen) ---
        r_outer = 400  # Volle Groesse, fuellt Icon-Bounding-Box komplett
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

        # --- Glossy Highlight (obere Haelfte, subtil) ---
        highlight = Image.new("RGBA", (hs, hs), (0, 0, 0, 0))
        hdraw = ImageDraw.Draw(highlight)
        hdraw.ellipse([160, 72, hs - 160, cx + 20], fill=(255, 255, 255, 30))
        img = Image.alpha_composite(img, highlight)
        draw = ImageDraw.Draw(img)

        # --- Mikrofon ---
        w = (255, 255, 255)

        # Schatten
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

        # Kapsel
        draw.rounded_rectangle([cx-76, 176, cx+76, 432], radius=76, fill=w)
        # U-Halterung
        draw.arc([cx-136, 352, cx+136, 544], 0, 180, fill=w, width=24)
        # Stiel
        draw.line([cx, 544, cx, 600], fill=w, width=24)
        # Basis
        draw.rounded_rectangle([cx-68, 588, cx+68, 616], radius=14, fill=w)

        # Resize auf Render-Groesse (200x200 bei 320x320 Frame)
        target = int(self.MIC_DISPLAY * self.RENDER_SIZE / self.DISPLAY_SIZE)
        return img.resize((target, target), Image.LANCZOS)

    def _generate_noise_texture(self, size, seed):
        """Multi-Oktaven Rausch-Textur (size x size) fuer Pixel-Displacement.

        Erzeugt smooth, turbulenz-aehnliches Noise-Feld mit 5 Oktaven.
        Verwendet nur numpy + PIL (keine externen Noise-Libraries).
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
        """2D Pixel-Displacement mit bilinearer Interpolation (pure numpy).

        Verschiebt jeden Pixel des Bildes unabhaengig basierend auf dx/dy Feldern.
        Dies simuliert SVG feDisplacementMap: der Ring wird pro Pixel verzerrt,
        nicht als Ganzes verschoben (wie bei der Polyline-Methode).
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
        """RGBA Frame (RENDER_SIZE) zu RGB (DISPLAY_SIZE) mit Transparenz konvertieren.

        Composited gegen Dunkelrot statt Schwarz/(1,1,1), damit halbtransparente
        Glow-Randpixel als dunkles Rot erscheinen statt als fast-Schwarz.
        """
        img = frame_rgba.resize((self.DISPLAY_SIZE, self.DISPLAY_SIZE), Image.LANCZOS)
        tc = self.TRANS_COLOR
        # Gegen Dunkelrot compositen (halbtransparente Pixel → dunkles Rot statt Schwarz)
        comp_bg = Image.new("RGBA", (self.DISPLAY_SIZE, self.DISPLAY_SIZE), (80, 15, 15, 255))
        composited = Image.alpha_composite(comp_bg, img)
        alpha = img.split()[3]
        mask = alpha.point(lambda x: 255 if x > 6 else 0)
        rgb = Image.new("RGB", (self.DISPLAY_SIZE, self.DISPLAY_SIZE), tc)
        rgb.paste(composited.convert("RGB"), mask=mask)
        return rgb

    def _prerender_frames(self):
        """Electric Border Frames vorrendern (optimiert).

        Alle Blur-Layer werden VOR dem Loop zu 2 Composite-Bildern zusammengefuegt.
        Pro Frame nur 2 Displacement-Ops (statt 6) und 0 Blur-Ops (statt 6+).
        """
        t0 = time.time()
        rs = self.RENDER_SIZE  # 320
        cx = rs // 2  # 160

        mic_r = int(self.MIC_DISPLAY / 2 * rs / self.DISPLAY_SIZE)
        border_r = mic_r + 1   # Ring sitzt direkt am Mic-Rand (fast kein Gap)
        outer_r = border_r + 18

        # --- Fill-Disc: Roter Hintergrund bis unter alle Rings ---
        # Deckt den gesamten Bereich hinter den Rings ab, auch bei max. Displacement.
        # Farbe passend zum Mic-Aussen-Gradient (205, 45, 45).
        fill_disc = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        fill_r = outer_r + 20  # Grosszuegig bis weit unter den Outer Ring
        ImageDraw.Draw(fill_disc).ellipse(
            [cx - fill_r, cx - fill_r, cx + fill_r, cx + fill_r],
            fill=(200, 42, 42, 255))
        fill_disc = fill_disc.filter(ImageFilter.GaussianBlur(radius=8))

        # --- Ring-Layer zeichnen ---
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

        # --- Pre-Composite: Alle Layer VOR dem Loop zusammenfuegen + blurren ---
        # Inner Composite (6 Layer → 1 Bild, nur 1 Displacement pro Frame)
        inner = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        inner = Image.alpha_composite(inner, ring_ambient.filter(ImageFilter.GaussianBlur(radius=20)))
        inner = Image.alpha_composite(inner, ring_glow.filter(ImageFilter.GaussianBlur(radius=10)))
        inner = Image.alpha_composite(inner, ring_glow.filter(ImageFilter.GaussianBlur(radius=4)))
        inner = Image.alpha_composite(inner, ring_glow.filter(ImageFilter.GaussianBlur(radius=2)))
        inner = Image.alpha_composite(inner, ring_sharp)
        inner = Image.alpha_composite(inner, ring_core)
        inner_arr = np.array(inner).astype(np.float32)

        # Outer Composite (2 Layer → 1 Bild, nur 1 Displacement pro Frame)
        outer = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        outer = Image.alpha_composite(outer, ring_outer_glow.filter(ImageFilter.GaussianBlur(radius=8)))
        outer = Image.alpha_composite(outer, ring_outer)
        outer_arr = np.array(outer).astype(np.float32)

        # --- Noise-Texturen ---
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

            # Breathing Pulse (2 Pulse pro 3s Loop)
            breath = 0.85 + 0.15 * math.sin(t * 2)

            # Core-Flash: 3 kurze Blitze pro Loop
            flash = 0.0
            for flash_phase in [1.05, 3.14, 5.24]:
                dist = abs(t - flash_phase)
                if dist > math.pi:
                    dist = 2 * math.pi - dist
                if dist < 0.3:
                    flash = max(flash, 1.0 - dist / 0.3)

            # --- Inner Displacement ---
            ox = int(pad + pan_radius * math.cos(t))
            oy = int(pad + pan_radius * math.sin(t))
            dx = noise_tex_x[oy:oy+rs, ox:ox+rs] * disp_scale

            ox2 = int(pad + pan_radius * math.cos(2*t + 1.5))
            oy2 = int(pad + pan_radius * math.sin(t + 0.8))
            dy = noise_tex_y[oy2:oy2+rs, ox2:ox2+rs] * disp_scale

            # --- Outer Displacement ---
            oxa = int(pad + pan_radius * 0.7 * math.cos(t * 0.7 + 2.0))
            oya = int(pad + pan_radius * 0.7 * math.sin(t * 0.7))
            dx_out = noise_tex_x2[oya:oya+rs, oxa:oxa+rs] * disp_scale_outer

            oxb = int(pad + pan_radius * 0.7 * math.cos(t * 1.3 + 1.0))
            oyb = int(pad + pan_radius * 0.7 * math.sin(t * 0.9 + 0.5))
            dy_out = noise_tex_y2[oyb:oyb+rs, oxb:oxb+rs] * disp_scale_outer

            # 2 Displacements (statt 6)
            disp_inner = self._apply_displacement(inner_arr, dx, dy)
            disp_outer = self._apply_displacement(outer_arr, dx_out, dy_out)

            # Breathing: Alpha des inneren Composites modulieren
            if breath < 1.0:
                disp_inner[:, :, 3] = (disp_inner[:, :, 3].astype(np.float32) * breath).astype(np.uint8)

            # Frame zusammensetzen: Fill-Disc → Electric Rings → Mic
            frame = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
            frame = Image.alpha_composite(frame, fill_disc)  # Gap fuellen
            frame = Image.alpha_composite(frame, Image.fromarray(disp_outer, mode="RGBA"))
            frame = Image.alpha_composite(frame, Image.fromarray(disp_inner, mode="RGBA"))

            # Core-Flash: Helligkeit kurz erhoehen
            if flash > 0:
                frame_arr = np.array(frame)
                boost = 1.0 + flash * 0.35
                frame_arr[:, :, :3] = np.minimum(255,
                    (frame_arr[:, :, :3].astype(np.float32) * boost)).astype(np.uint8)
                frame = Image.fromarray(frame_arr, mode="RGBA")

            # Mic-Icon (nicht displaced, bleibt scharf)
            if self._mic_rgba:
                mic_rs = self._mic_rgba.size[0]
                offset = cx - mic_rs // 2
                frame.paste(self._mic_rgba, (offset, offset), self._mic_rgba)

            frames.append(self._make_display_frame(frame))

        self._frames = frames
        self._frames_ready = True
        duration = time.time() - t0
        append_to_history(
            f"[STARTUP] Electric Border vorgerendert: {self.NUM_FRAMES} Frames in {duration:.1f}s")

    def start(self):
        """Overlay in eigenem Thread starten."""
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self):
        import tkinter as tk
        from PIL import ImageTk

        self.root = tk.Tk()
        self.root.withdraw()

        # Mic-Icon vorrendern
        self._mic_rgba = self._create_mic_icon()
        self._t0 = time.time()

        # Statisches Fallback-Frame (nur Mic-Icon mit Fill-Disc, ohne Electric Border)
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

        # Pre-Rendering in separatem Thread starten (laeuft parallel zum Modell-Laden)
        prerender_thread = threading.Thread(target=self._prerender_frames, daemon=True)
        prerender_thread.start()

        monitors = get_monitors()

        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x20
        WS_EX_LAYERED = 0x80000

        # --- Rote Balken auf allen Monitoren ---
        for i, (left, top, right, bottom) in enumerate(monitors):
            win = tk.Toplevel(self.root)
            title = f"WhisperREC_{i}"
            win.title(title)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.configure(bg="#ef4444")

            width = right - left
            win.geometry(f"{width}x{self.BAR_HEIGHT}+{left}+{top}")

            # Click-through
            win.update_idletasks()
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style | WS_EX_TRANSPARENT)

            win.withdraw()
            self._windows.append(win)

        # --- Electric Border Fenster (160x160, oben links) ---
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

        # Initialer leerer Frame
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

        # Polling: recording-Status alle 100ms pruefen
        self._poll()
        self.root.mainloop()

    def _poll(self):
        """Overlay ein-/ausblenden basierend auf Aufnahme-Status."""
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

        # Dashboard toggle (von Tray Links-Klick)
        if _dashboard_toggle.is_set():
            _dashboard_toggle.clear()
            self._toggle_dashboard()

        # Dashboard automatisch schliessen bei Aufnahme-Start
        if recording and self._dashboard_visible:
            self._destroy_dashboard()

        self.root.after(100, self._poll)

    def _animate(self):
        """Animation: Pre-gerenderte Frames durchcyclen + Balken-Pulse bei ~30 FPS."""
        if not self._visible:
            return

        from PIL import ImageTk

        # Frame auswaehlen: Calm Mode = immer statisch, sonst Electric Border
        if calm_mode or not self._frames_ready:
            frame = self._static_frame
        else:
            elapsed = time.time() - self._t0
            idx = int(elapsed * 30) % self.NUM_FRAMES
            frame = self._frames[idx]

        self._orb_photo = ImageTk.PhotoImage(frame)
        self._orb_label.configure(image=self._orb_photo)

        # Balken-Pulse (subtil, ~3s Zyklus)
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
    # Dashboard Popup
    # ================================================================

    def _toggle_dashboard(self):
        """Dashboard ein-/ausblenden."""
        if self._dashboard_visible:
            self._destroy_dashboard()
        else:
            self._create_dashboard()

    def _destroy_dashboard(self):
        """Dashboard schliessen und aufraeumen."""
        if self._dashboard_win:
            try:
                self._dashboard_win.destroy()
            except Exception:
                pass
            self._dashboard_win = None
        self._dashboard_visible = False

    def _create_dashboard(self):
        """Premium Dashboard Popup mit Stats, Verlauf und Controls."""
        import tkinter as tk

        if self._dashboard_win:
            self._destroy_dashboard()
            return

        # ── Design Tokens ──
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

        # Aeusserer Rahmen (1px Border)
        inner = tk.Frame(win, bg=BG)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        # Rote Akzentlinie oben
        tk.Frame(inner, bg=ACCENT, height=2).pack(fill="x")

        # Hauptbereich
        main = tk.Frame(inner, bg=BG)
        main.pack(fill="both", expand=True, padx=24, pady=(20, 20))

        # Mindestbreite erzwingen
        spacer = tk.Frame(main, bg=BG, height=0, width=WIDTH - 50)
        spacer.pack(fill="x")
        spacer.pack_propagate(False)

        # ── Header ──
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

        # ── Status-Zeile ──
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

        # ── Trennlinie ──
        tk.Frame(main, bg=DIVIDER, height=1).pack(fill="x", pady=(0, 16))

        # ── Statistik-Karten ──
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

            # Farbiger Akzent-Balken links
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

        # ── Trennlinie ──
        tk.Frame(main, bg=DIVIDER, height=1).pack(fill="x", pady=(0, 16))

        # ── Verlauf ──
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

            COPIED_BG = "#1a2a1a"  # Kurzer gruener Flash nach Kopieren

            for entry in reversed(logs):
                row = tk.Frame(main, bg=BG, cursor="hand2")
                row.pack(fill="x", pady=1)

                # Datums-Prefix fuer aeltere Eintraege
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

                # Text-Vorschau (gekuerzt)
                preview = entry["text"]
                max_chars = 30 if entry["date"] != today_str else 36
                if len(preview) > max_chars:
                    preview = preview[:max_chars] + "\u2026"
                tk.Label(row, text=preview, font=("Segoe UI", 9),
                         fg=TEXT2, bg=BG, anchor="w").pack(side="left", fill="x")

                # Klick kopiert den vollen Text in die Zwischenablage
                full_text = entry["text"]
                all_widgets = [row] + list(row.winfo_children())

                def copy_text(e, txt=full_text, ws=all_widgets):
                    pyperclip.copy(txt)
                    # Gruener Flash als Bestaetigung
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

        # ── Trennlinie ──
        tk.Frame(main, bg=DIVIDER, height=1).pack(fill="x", pady=(14, 16))

        # ── Aktions-Buttons ──
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

        # ── Positionierung & Animation ──
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

        # Slide-up + Fade-in
        self._dash_animate(x, win_w, win_h, y_end, y_start, 0)

    def _dash_animate(self, x, w, h, y_end, y_start, step):
        """Ease-out Slide-up + Fade-in Animation."""
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
        """Calm Mode umschalten und Dashboard neu aufbauen."""
        global calm_mode
        calm_mode = not calm_mode
        save_config()
        self._destroy_dashboard()
        self.root.after(50, self._create_dashboard)

    def _dash_restart(self):
        """Neustart aus Dashboard."""
        self._destroy_dashboard()
        if tray_icon:
            on_restart(tray_icon, None)

    def _dash_quit(self):
        """Beenden aus Dashboard."""
        self._destroy_dashboard()
        if tray_icon:
            on_quit(tray_icon, None)


def load_model():
    """Whisper-Modell beim Start laden."""
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
        append_to_history(f"[STARTUP] Modell geladen in {load_time:.1f}s")
        update_tray("Bereit (CTRL+ALT+D)", create_icon_idle())
        play_ready_sound()
    except Exception:
        # Fehler in Logdatei schreiben (pythonw hat keine Konsole)
        log_path = os.path.join(os.path.dirname(__file__), "whisper-error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        update_tray("FEHLER - siehe whisper-error.log", create_icon_loading())


def audio_callback(indata, frames, time_info, status):
    """Wird aufgerufen waehrend der Aufnahme."""
    global audio_overflow_count, audio_level
    if status:
        # Input overflow = Audio-Daten gingen verloren (Buffer zu klein)
        audio_overflow_count += 1
    if recording:
        audio_chunks.append(indata.copy())
        # RMS-Pegel fuer Orb-Animation berechnen (0.0-1.0)
        audio_level = min(1.0, np.sqrt(np.mean(indata**2)) * 5.0)


def start_recording():
    """Aufnahme starten."""
    global recording, audio_chunks, audio_overflow_count, stream, target_window
    if recording:
        return

    target_window = user32.GetForegroundWindow()

    # Sound VOR der Aufnahme (damit der Beep nicht mitaufgenommen wird)
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
    """Aufnahme stoppen, transkribieren, Text einfuegen."""
    global recording, stream, audio_level
    if not recording:
        return

    recording = False
    audio_level = 0.0

    if stream:
        stream.stop()
        stream.close()
        stream = None

    # Sound NACH dem Stoppen (Aufnahme ist bereits beendet)
    play_stop_sound()

    update_tray("Transkribiere...", create_icon_loading())

    if not audio_chunks:
        update_tray("Bereit (CTRL+ALT+D)", create_icon_idle())
        return

    chunk_count = len(audio_chunks)
    audio = np.concatenate(audio_chunks, axis=0).flatten()
    duration = len(audio) / SAMPLE_RATE

    # Overflow-Warnung ins Log schreiben
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

        # Generator komplett konsumieren (verhindert Datenverlust bei Iteration-Fehlern)
        segments_list = list(segments)
        t_transcribe = time.time() - t_start

        parts = filter_hallucinations(segments_list)
        text = " ".join(parts).strip()
        text = apply_spoken_punctuation(text)
        text = apply_word_corrections(text)
        text = remove_trailing_period(text)

        # Performance-Log
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
    """Keyboard-Loop in eigenem Thread."""
    # Warte bis Modell geladen
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
    """Neustart ueber Tray-Menue: neue Instanz starten, dann aktuelle beenden.

    Verwendet pythonw (nicht cmd.exe) fuer den verzögerten Start,
    damit kein Terminal-Fenster sichtbar wird.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(script_dir, "whisper-dictate.py")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    # pythonw -c fuer den Sleep: komplett unsichtbar, kein cmd.exe noetig
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
    """Calm Mode umschalten (statisches Mic-Icon statt Electric Border)."""
    global calm_mode
    calm_mode = not calm_mode
    save_config()


def on_activate(icon, item):
    """Dashboard oeffnen/schliessen bei Links-Klick auf Tray-Icon."""
    _dashboard_toggle.set()


def on_quit(icon, item):
    """Beenden ueber Tray-Menue."""
    icon.stop()
    os._exit(0)


def main():
    global tray_icon

    # Autostart sicherstellen (Registry Run-Key setzen, alte .lnk aufraeumen)
    ensure_autostart()

    # Config laden (calm_mode etc.)
    load_config()

    # Tray-Icon erstellen (Menu nur fuer default action, natives Menue deaktiviert)
    menu = pystray.Menu(
        pystray.MenuItem("Dashboard", on_activate, default=True, visible=False),
    )
    tray_icon = pystray.Icon(
        "whisper-dictate",
        create_icon_loading(),
        "Whisper Diktiertool - Lade Modell...",
        menu,
    )

    # Rechts-Klick soll Dashboard oeffnen statt natives Menue (wie Links-Klick)
    _original_on_notify = tray_icon._on_notify
    def _patched_on_notify(wparam, lparam):
        if lparam == 0x0205:  # WM_RBUTTONUP: Dashboard statt Popup-Menu
            tray_icon()
        else:
            _original_on_notify(wparam, lparam)
    tray_icon._on_notify = _patched_on_notify

    # Recording-Overlay (floating "REC" Anzeige)
    overlay = RecordingOverlay()
    overlay.start()

    # Hotkey-Loop und Modell-Laden in Hintergrund-Threads
    hotkey_thread = threading.Thread(target=hotkey_loop, daemon=True)
    hotkey_thread.start()

    model_thread = threading.Thread(target=load_model, daemon=True)
    model_thread.start()

    # Tray-Icon blockiert den Hauptthread (muss so sein)
    tray_icon.run()


if __name__ == "__main__":
    main()
