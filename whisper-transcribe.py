"""
Whisper Transcription - Audio to Text (German)
===============================================
Uses faster-whisper with the large-v3 model on GPU.

Usage:
    python whisper-transcribe.py "path/to/audiofile.mp3"
    python whisper-transcribe.py                              (prompts for file)

Supported formats: mp3, wav, m4a, flac, ogg, wma, aac, mp4, mkv, avi
"""

import sys
import os
import time
import sysconfig
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
VENV_DIR = PROJECT_DIR / ".venv"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"


def use_project_venv() -> None:
    """Relaunch with the project virtual environment when it is available."""
    if VENV_PYTHON.exists() and Path(sys.prefix).resolve() != VENV_DIR.resolve():
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


def configure_cuda_dlls() -> None:
    """Make the CUDA libraries installed with faster-whisper visible to CTranslate2."""
    site_packages = Path(sysconfig.get_path("purelib"))
    dll_dirs = [site_packages / "nvidia" / library / "bin" for library in ("cublas", "cudnn")]
    available_dirs = [dll_dir for dll_dir in dll_dirs if dll_dir.is_dir()]
    for dll_dir in available_dirs:
        os.add_dll_directory(str(dll_dir))
    if available_dirs:
        os.environ["PATH"] = os.pathsep.join(map(str, available_dirs)) + os.pathsep + os.environ.get("PATH", "")


use_project_venv()
configure_cuda_dlls()


def transcribe(audio_path: str) -> None:
    from faster_whisper import WhisperModel

    audio_file = Path(audio_path)
    if not audio_file.exists():
        print(f"Error: File not found: {audio_file}")
        sys.exit(1)

    print(f"File:  {audio_file.name}")
    print(f"Size: {audio_file.stat().st_size / (1024*1024):.1f} MB")
    print()

    # Load model (first run downloads ~3GB)
    print("Loading Whisper large-v3 model (GPU)...")
    print("(First run: ~3 GB download, then instantly ready)")
    print()

    model = WhisperModel(
        "large-v3",
        device="cuda",
        compute_type="float16",  # Optimal for RTX 4060
    )

    print("Transcribing...")
    start = time.time()

    segments, info = model.transcribe(
        str(audio_file),
        language="de",
        beam_size=5,
        vad_filter=True,           # Filters out silence
        vad_parameters=dict(
            min_silence_duration_ms=500,
        ),
    )

    # Collect result
    full_text = []
    segments_list = []

    for segment in segments:
        segments_list.append(segment)
        full_text.append(segment.text.strip())
        # Live output
        mins, secs = divmod(int(segment.start), 60)
        print(f"  [{mins:02d}:{secs:02d}] {segment.text.strip()}")

    elapsed = time.time() - start
    duration_mins = info.duration / 60

    print()
    print(f"Done! {duration_mins:.1f} min audio transcribed in {elapsed:.1f} sec")
    print(f"Speed: {info.duration / elapsed:.1f}x real-time")
    print()

    # Save text file
    output_path = audio_file.with_suffix(".txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(" ".join(full_text))

    print(f"Saved: {output_path}")

    # Optional: save SRT subtitles
    srt_path = audio_file.with_suffix(".srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments_list, 1):
            start_h, start_r = divmod(seg.start, 3600)
            start_m, start_s = divmod(start_r, 60)
            end_h, end_r = divmod(seg.end, 3600)
            end_m, end_s = divmod(end_r, 60)
            f.write(f"{i}\n")
            f.write(f"{int(start_h):02d}:{int(start_m):02d}:{start_s:06.3f}".replace(".", ","))
            f.write(f" --> ")
            f.write(f"{int(end_h):02d}:{int(end_m):02d}:{end_s:06.3f}".replace(".", ","))
            f.write(f"\n{seg.text.strip()}\n\n")

    print(f"Subtitles: {srt_path}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # File path as argument
        audio_file = sys.argv[1]
    else:
        # Ask for file interactively
        print("=== Whisper Transcription ===")
        print()
        audio_file = input("Audio file (enter or drag path): ").strip().strip('"')

    if not audio_file:
        print("No file provided.")
        sys.exit(1)

    transcribe(audio_file)
