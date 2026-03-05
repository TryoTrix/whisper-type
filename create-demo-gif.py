"""
Demo GIF Generator for Whisper Type
====================================
Creates an animated GIF showing the dictation workflow with Electric Border.
Renders actual Electric Border frames from whisper-dictate.py rendering logic.

Run: python create-demo-gif.py
Output: demo.gif (for README)
"""

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import numpy as np
import math
import os

# ============================================================
# CONFIG
# ============================================================
WIDTH = 800
HEIGHT = 500
FPS = 15

# Colors (dark theme)
BG = (13, 17, 23)
EDITOR_BG = (22, 27, 34)
BAR_TOP = (30, 35, 44)
TEXT_WHITE = (230, 237, 243)
TEXT_DIM = (125, 133, 144)
TEXT_GREEN = (63, 185, 80)
TEXT_YELLOW = (210, 153, 34)
REC_RED = (239, 68, 68)
ACCENT_BLUE = (56, 139, 253)
CURSOR_COLOR = (230, 237, 243)
HOTKEY_BG = (52, 59, 72)
HOTKEY_BORDER = (80, 90, 105)

# Electric Border config
EB_RENDER = 240   # Render size (high res)
EB_DISPLAY = 120  # Display size in GIF
EB_FRAMES = 30    # Frames for the GIF loop (subset of full 90)


def get_font(size):
    """Try system monospace fonts, fall back to default."""
    for path in [
        "C:/Windows/Fonts/consola.ttf",
        "C:/Windows/Fonts/cascadiamono.ttf",
        "C:/Windows/Fonts/cour.ttf",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


FONT = get_font(20)
FONT_SM = get_font(15)
FONT_TITLE = get_font(16)


# ============================================================
# ELECTRIC BORDER RENDERING (extracted from whisper-dictate.py)
# ============================================================

def generate_noise_texture(size, seed):
    """Multi-octave noise texture for displacement."""
    rng = np.random.default_rng(seed)
    result = np.zeros((size, size), dtype=np.float32)
    for octave in range(5):
        grid_size = 4 * (2 ** octave)
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


def apply_displacement(img_arr, dx, dy):
    """2D pixel displacement with bilinear interpolation."""
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


def create_mic_icon(render_size):
    """Render mic icon at high resolution, return RGBA."""
    hs = 600
    img = Image.new("RGBA", (hs, hs), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cx = 300

    # Red gradient circle
    r_outer = 300
    for i in range(30):
        t = i / 29
        inset = int(12 + t * 132)
        r = int(205 + t * 34)
        g = int(45 + t * 23)
        b = int(45 + t * 23)
        draw.ellipse([cx - r_outer + inset, cx - r_outer + inset,
                      cx + r_outer - inset, cx + r_outer - inset], fill=(r, g, b))

    # Glossy highlight
    highlight = Image.new("RGBA", (hs, hs), (0, 0, 0, 0))
    ImageDraw.Draw(highlight).ellipse([120, 54, hs - 120, cx + 15], fill=(255, 255, 255, 30))
    img = Image.alpha_composite(img, highlight)
    draw = ImageDraw.Draw(img)

    # Microphone
    w = (255, 255, 255)
    draw.rounded_rectangle([cx - 57, 132, cx + 57, 324], radius=57, fill=w)
    draw.arc([cx - 102, 264, cx + 102, 408], 0, 180, fill=w, width=18)
    draw.line([cx, 408, cx, 450], fill=w, width=18)
    draw.rounded_rectangle([cx - 51, 441, cx + 51, 462], radius=10, fill=w)

    target = int(render_size * 0.62)
    return img.resize((target, target), Image.LANCZOS)


def render_electric_border_frames():
    """Pre-render Electric Border frames for the demo GIF."""
    print("Rendering Electric Border frames...")
    rs = EB_RENDER
    cx = rs // 2

    mic_rgba = create_mic_icon(rs)
    mic_display = int(rs * 0.5)
    mic_r = mic_display // 2
    border_r = mic_r + 1
    outer_r = border_r + 14

    # Fill disc
    fill_disc = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
    fill_r = outer_r + 16
    ImageDraw.Draw(fill_disc).ellipse(
        [cx - fill_r, cx - fill_r, cx + fill_r, cx + fill_r],
        fill=(200, 42, 42, 255))
    fill_disc = fill_disc.filter(ImageFilter.GaussianBlur(radius=6))

    # Ring layers
    ring_core = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
    ImageDraw.Draw(ring_core).ellipse(
        [cx - border_r, cx - border_r, cx + border_r, cx + border_r],
        outline=(255, 235, 220, 245), width=2)

    ring_sharp = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
    ImageDraw.Draw(ring_sharp).ellipse(
        [cx - border_r, cx - border_r, cx + border_r, cx + border_r],
        outline=(255, 120, 80, 220), width=4)

    ring_glow = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
    ImageDraw.Draw(ring_glow).ellipse(
        [cx - border_r, cx - border_r, cx + border_r, cx + border_r],
        outline=(239, 68, 68, 200), width=10)

    ring_ambient = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
    ImageDraw.Draw(ring_ambient).ellipse(
        [cx - border_r, cx - border_r, cx + border_r, cx + border_r],
        outline=(239, 50, 50, 160), width=16)

    ring_outer = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
    ImageDraw.Draw(ring_outer).ellipse(
        [cx - outer_r, cx - outer_r, cx + outer_r, cx + outer_r],
        outline=(255, 100, 80, 140), width=2)

    ring_outer_glow = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
    ImageDraw.Draw(ring_outer_glow).ellipse(
        [cx - outer_r, cx - outer_r, cx + outer_r, cx + outer_r],
        outline=(239, 60, 60, 120), width=6)

    # Pre-composite
    inner = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
    inner = Image.alpha_composite(inner, ring_ambient.filter(ImageFilter.GaussianBlur(radius=15)))
    inner = Image.alpha_composite(inner, ring_glow.filter(ImageFilter.GaussianBlur(radius=8)))
    inner = Image.alpha_composite(inner, ring_glow.filter(ImageFilter.GaussianBlur(radius=3)))
    inner = Image.alpha_composite(inner, ring_sharp)
    inner = Image.alpha_composite(inner, ring_core)
    inner_arr = np.array(inner).astype(np.float32)

    outer = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
    outer = Image.alpha_composite(outer, ring_outer_glow.filter(ImageFilter.GaussianBlur(radius=6)))
    outer = Image.alpha_composite(outer, ring_outer)
    outer_arr = np.array(outer).astype(np.float32)

    # Noise textures
    pad = 80
    tex_size = rs + pad * 2
    noise_tex_x = generate_noise_texture(tex_size, seed=42)
    noise_tex_y = generate_noise_texture(tex_size, seed=137)
    noise_tex_x2 = generate_noise_texture(tex_size, seed=73)
    noise_tex_y2 = generate_noise_texture(tex_size, seed=211)

    disp_scale = 12.0
    disp_scale_outer = 8.0
    pan_radius = 48

    frames = []
    for fi in range(EB_FRAMES):
        t = fi / EB_FRAMES * 2 * math.pi
        breath = 0.85 + 0.15 * math.sin(t * 2)

        flash = 0.0
        for flash_phase in [1.05, 3.14, 5.24]:
            dist = abs(t - flash_phase)
            if dist > math.pi:
                dist = 2 * math.pi - dist
            if dist < 0.3:
                flash = max(flash, 1.0 - dist / 0.3)

        ox = int(pad + pan_radius * math.cos(t))
        oy = int(pad + pan_radius * math.sin(t))
        dx = noise_tex_x[oy:oy + rs, ox:ox + rs] * disp_scale
        ox2 = int(pad + pan_radius * math.cos(2 * t + 1.5))
        oy2 = int(pad + pan_radius * math.sin(t + 0.8))
        dy = noise_tex_y[oy2:oy2 + rs, ox2:ox2 + rs] * disp_scale

        oxa = int(pad + pan_radius * 0.7 * math.cos(t * 0.7 + 2.0))
        oya = int(pad + pan_radius * 0.7 * math.sin(t * 0.7))
        dx_out = noise_tex_x2[oya:oya + rs, oxa:oxa + rs] * disp_scale_outer
        oxb = int(pad + pan_radius * 0.7 * math.cos(t * 1.3 + 1.0))
        oyb = int(pad + pan_radius * 0.7 * math.sin(t * 0.9 + 0.5))
        dy_out = noise_tex_y2[oyb:oyb + rs, oxb:oxb + rs] * disp_scale_outer

        disp_inner = apply_displacement(inner_arr, dx, dy)
        disp_outer = apply_displacement(outer_arr, dx_out, dy_out)

        if breath < 1.0:
            disp_inner[:, :, 3] = (disp_inner[:, :, 3].astype(np.float32) * breath).astype(np.uint8)

        frame = Image.new("RGBA", (rs, rs), (0, 0, 0, 0))
        frame = Image.alpha_composite(frame, fill_disc)
        frame = Image.alpha_composite(frame, Image.fromarray(disp_outer, mode="RGBA"))
        frame = Image.alpha_composite(frame, Image.fromarray(disp_inner, mode="RGBA"))

        if flash > 0:
            frame_arr = np.array(frame)
            boost = 1.0 + flash * 0.35
            frame_arr[:, :, :3] = np.minimum(255,
                (frame_arr[:, :, :3].astype(np.float32) * boost)).astype(np.uint8)
            frame = Image.fromarray(frame_arr, mode="RGBA")

        if mic_rgba:
            mic_rs = mic_rgba.size[0]
            offset = cx - mic_rs // 2
            frame.paste(mic_rgba, (offset, offset), mic_rgba)

        # Resize to display size
        display = frame.resize((EB_DISPLAY, EB_DISPLAY), Image.LANCZOS)
        frames.append(display)
        if (fi + 1) % 10 == 0:
            print(f"  Frame {fi + 1}/{EB_FRAMES}")

    print(f"  Done: {len(frames)} Electric Border frames")
    return frames


# ============================================================
# SCENE HELPERS
# ============================================================

def draw_base(draw):
    """Draw the editor chrome."""
    draw.rectangle([0, 0, WIDTH, 38], fill=BAR_TOP)
    for i, color in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        draw.ellipse([16 + i * 24, 12, 28 + i * 24, 24], fill=color)
    draw.text((WIDTH // 2 - 60, 10), "whisper-type", fill=TEXT_DIM, font=FONT_TITLE)
    draw.rectangle([0, 38, WIDTH, HEIGHT], fill=EDITOR_BG)
    draw.rectangle([0, 38, 50, HEIGHT], fill=BG)


def draw_line_numbers(draw, count=15):
    for i in range(count):
        y = 52 + i * 26
        if y > HEIGHT - 20:
            break
        draw.text((15, y), str(i + 1), fill=TEXT_DIM, font=FONT_SM)


def draw_hotkey_badge(draw, text, x, y):
    bbox = FONT.getbbox(text)
    tw = bbox[2] - bbox[0]
    pad = 10
    draw.rounded_rectangle(
        [x, y, x + tw + pad * 2, y + 30],
        radius=6, fill=HOTKEY_BG, outline=HOTKEY_BORDER
    )
    draw.text((x + pad, y + 4), text, fill=TEXT_WHITE, font=FONT)


def draw_rec_bar(draw, phase=0):
    """Red recording bar with pulse."""
    factor = (math.sin(phase) + 1) / 2
    r = int(185 + factor * 54)
    g = int(28 + factor * 40)
    b = int(28 + factor * 40)
    draw.rectangle([0, 38, WIDTH, 46], fill=(r, g, b))


def draw_tray_icon(draw, color, x=WIDTH - 35, y=10):
    draw.ellipse([x, y, x + 16, y + 16], fill=color)


def draw_cursor(draw, x, y):
    draw.rectangle([x, y, x + 2, y + 22], fill=CURSOR_COLOR)


def draw_status_line(draw, text, color=TEXT_DIM):
    draw.rectangle([0, HEIGHT - 28, WIDTH, HEIGHT], fill=BAR_TOP)
    draw.text((12, HEIGHT - 24), text, fill=color, font=FONT_SM)


def draw_editor_text(draw):
    """Common editor text lines."""
    draw.text((62, 52), "Meeting notes:", fill=TEXT_YELLOW, font=FONT)
    draw.text((62, 78), "- Budget approved for Q2", fill=TEXT_WHITE, font=FONT)
    draw.text((62, 104), "- New hire starts March 3rd", fill=TEXT_WHITE, font=FONT)


# ============================================================
# SCENES
# ============================================================

def scene_idle(cursor_on=True):
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw_base(draw)
    draw_line_numbers(draw)
    draw_tray_icon(draw, TEXT_GREEN)
    draw_editor_text(draw)
    draw.text((62, 130), "- ", fill=TEXT_WHITE, font=FONT)
    if cursor_on:
        draw_cursor(draw, 82, 130)
    draw_status_line(draw, "Ready  |  CTRL+ALT+D to dictate")
    return img


def scene_hotkey():
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw_base(draw)
    draw_line_numbers(draw)
    draw_tray_icon(draw, TEXT_GREEN)
    draw_editor_text(draw)
    draw.text((62, 130), "- ", fill=TEXT_WHITE, font=FONT)

    oy = 200
    draw.rounded_rectangle(
        [WIDTH // 2 - 150, oy, WIDTH // 2 + 150, oy + 50],
        radius=10, fill=(30, 35, 44), outline=ACCENT_BLUE
    )
    draw_hotkey_badge(draw, "CTRL", WIDTH // 2 - 130, oy + 10)
    draw.text((WIDTH // 2 - 62, oy + 14), "+", fill=TEXT_DIM, font=FONT)
    draw_hotkey_badge(draw, "ALT", WIDTH // 2 - 45, oy + 10)
    draw.text((WIDTH // 2 + 17, oy + 14), "+", fill=TEXT_DIM, font=FONT)
    draw_hotkey_badge(draw, "D", WIDTH // 2 + 35, oy + 10)
    draw_status_line(draw, "Ready  |  CTRL+ALT+D to dictate")
    return img


def scene_recording(eb_frame, phase=0):
    """Recording scene with actual Electric Border animation."""
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw_base(draw)
    draw_rec_bar(draw, phase)
    draw_line_numbers(draw)
    draw_tray_icon(draw, REC_RED)
    draw_editor_text(draw)
    draw.text((62, 130), "- ", fill=TEXT_WHITE, font=FONT)

    # Electric Border mic icon (top-left of editor area, like the real overlay)
    eb_x = 62
    eb_y = 50
    # Composite RGBA onto RGB
    if eb_frame:
        img.paste(eb_frame, (eb_x, eb_y), eb_frame)

    # Recording text next to the mic
    draw.text((eb_x + EB_DISPLAY + 20, eb_y + 30), "Recording...", fill=REC_RED, font=FONT)

    # Sound wave visualization
    wave_y = eb_y + 70
    wave_x_start = eb_x + EB_DISPLAY + 20
    for i in range(30):
        h = int(4 + 10 * abs(math.sin((i + phase * 3) * 0.35)))
        x = wave_x_start + i * 6
        draw.rectangle([x, wave_y - h, x + 3, wave_y + h], fill=REC_RED)

    draw_status_line(draw, "Recording...  |  CTRL+ALT+D to stop", REC_RED)
    return img


def scene_transcribing():
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw_base(draw)
    draw_line_numbers(draw)
    draw_tray_icon(draw, (156, 163, 175))
    draw_editor_text(draw)
    draw.text((62, 130), "- ", fill=TEXT_WHITE, font=FONT)
    draw.text((WIDTH // 2 - 80, 220), "Transcribing...", fill=TEXT_YELLOW, font=FONT)
    draw_status_line(draw, "Transcribing...  |  GPU processing", TEXT_YELLOW)
    return img


def scene_result(chars_visible=0):
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw_base(draw)
    draw_line_numbers(draw)
    draw_tray_icon(draw, TEXT_GREEN)
    draw_editor_text(draw)

    full_text = "Next step: finalize the proposal"
    visible = full_text[:chars_visible]
    draw.text((62, 130), "- " + visible, fill=TEXT_GREEN, font=FONT)
    bbox = FONT.getbbox("- " + visible)
    draw_cursor(draw, 62 + bbox[2] - bbox[0], 130)

    if chars_visible >= len(full_text):
        draw_status_line(draw, "Done  |  0.6s  |  Ready for next dictation", TEXT_GREEN)
    else:
        draw_status_line(draw, "Inserting text...", TEXT_GREEN)
    return img


def scene_done():
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    draw_base(draw)
    draw_line_numbers(draw)
    draw_tray_icon(draw, TEXT_GREEN)
    draw_editor_text(draw)
    full = "- Next step: finalize the proposal"
    draw.text((62, 130), full, fill=TEXT_WHITE, font=FONT)
    draw_cursor(draw, 62 + FONT.getbbox(full)[2], 130)
    draw_status_line(draw, "Done  |  0.6s  |  Ready for next dictation", TEXT_GREEN)
    return img


# ============================================================
# ASSEMBLE GIF
# ============================================================

def main():
    # Pre-render Electric Border frames
    eb_frames = render_electric_border_frames()

    frames = []
    durations = []

    # Act 1: Idle editor with cursor blinking (1.5s)
    for i in range(8):
        frames.append(scene_idle(cursor_on=i % 3 != 2))
        durations.append(190)

    # Act 2: Hotkey press (0.8s)
    for _ in range(4):
        frames.append(scene_hotkey())
        durations.append(200)

    # Act 3: Recording with Electric Border (4s - show the animation!)
    for i in range(EB_FRAMES):
        phase = i / EB_FRAMES * 2 * math.pi
        frames.append(scene_recording(eb_frames[i], phase=phase))
        durations.append(133)  # ~7.5fps for GIF (smooth enough, small file)

    # Act 4: Hotkey press again to stop (0.6s)
    for _ in range(3):
        frames.append(scene_hotkey())
        durations.append(200)

    # Act 5: Transcribing (1s)
    for _ in range(5):
        frames.append(scene_transcribing())
        durations.append(200)

    # Act 6: Text appearing character by character (2s)
    full_text = "Next step: finalize the proposal"
    step = max(1, len(full_text) // 12)
    for i in range(0, len(full_text) + 1, step):
        frames.append(scene_result(chars_visible=min(i, len(full_text))))
        durations.append(100)
    frames.append(scene_result(chars_visible=len(full_text)))
    durations.append(100)

    # Act 7: Done, hold (2s)
    for _ in range(10):
        frames.append(scene_done())
        durations.append(200)

    # Save GIF
    output = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.gif")
    frames[0].save(
        output,
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )

    size_kb = os.path.getsize(output) / 1024
    print(f"\nCreated {output}")
    print(f"  {len(frames)} frames, {size_kb:.0f} KB")


if __name__ == "__main__":
    main()
