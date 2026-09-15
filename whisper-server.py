"""
Whisper-Type local transcription server (development use, LAN only)
==================================================================
Upload an audio file, get text plus segment and word timestamps back.
Built for testing a phone app's upload flow against this PC before renting a server.

Start:
    python whisper-server.py                 # 0.0.0.0:8765, settings from whisper-config.json
    python whisper-server.py --port 9000 --token secret123
    python whisper-server.py --device cpu --compute-type int8 --threads 4   # simulate a CPU server

Endpoints (CORS enabled for browser clients):
    GET  /health              -> {"status": "ok", "model": ..., "queue": n}
    POST /jobs                -> 202 {"job_id": "...", "status": "queued"}   body = audio file
                                 (multipart/form-data field "audio" or raw audio bytes;
                                  any format ffmpeg decodes: wav, webm/opus, mp4/aac, m4a, ogg, mp3)
                                 query: ?language=de&prompt=...&word_timestamps=1
    GET  /jobs/<id>           -> {"status": "queued|running|done|error", "text": ..., "segments": [...], "words": [...]}
    DELETE /jobs/<id>         -> {"deleted": true}
    POST /transcribe          -> like /jobs but waits and returns the finished result (short clips)

Audio is deleted right after decoding; only the result stays in memory (until --result-ttl expires).
No authentication unless --token is given (then send "Authorization: Bearer <token>").
"""

import argparse
import email
import email.policy
import json
import os
import queue
import re
import secrets
import socket
import sys
import sysconfig
import tempfile
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import numpy as np

# Make NVIDIA DLLs visible for CUDA (same lookup as whisper-dictate.py)
_site_packages = sysconfig.get_path("purelib")
_dll_dirs = []
for _lib in ("cublas", "cudnn"):
    _dll_dir = os.path.join(_site_packages, "nvidia", _lib, "bin")
    if os.path.isdir(_dll_dir):
        os.add_dll_directory(_dll_dir)
        _dll_dirs.append(_dll_dir)
if _dll_dirs:
    os.environ["PATH"] = os.pathsep.join(_dll_dirs) + os.pathsep + os.environ.get("PATH", "")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG = {}
ARGS = None
MODEL = None
BATCHED = None
JOBS = {}
JOBS_LOCK = threading.Lock()
JOB_QUEUE = queue.Queue()


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- config / model
def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_model():
    global MODEL, BATCHED
    from faster_whisper import WhisperModel, BatchedInferencePipeline

    model_config = CONFIG["model"]
    download_root = model_config.get("download_root")
    download_root = os.path.expanduser(str(download_root)) if download_root else None
    device = ARGS.device or str(model_config["device"])
    compute_type = ARGS.compute_type or str(model_config["compute_type"])
    kwargs = {}
    if ARGS.threads:
        kwargs["cpu_threads"] = ARGS.threads
    t0 = time.time()
    MODEL = WhisperModel(ARGS.model or str(model_config["size"]), download_root=download_root,
                         device=device, compute_type=compute_type, **kwargs)
    load_time = time.time() - t0
    batch_size = ARGS.batch_size if ARGS.batch_size is not None else int(CONFIG["transcription"].get("batch_size", 8))
    if batch_size > 1:
        BATCHED = BatchedInferencePipeline(MODEL)
    t0 = time.time()
    warmup()
    log(f"[STARTUP] Model {ARGS.model or model_config['size']} loaded on {device}/{compute_type} in {load_time:.1f}s (warm-up {time.time() - t0:.1f}s), batch_size={batch_size}")


def warmup():
    """One silent transcription so CUDA kernels / CPU threads are ready before the first job."""
    try:
        silence = np.zeros(int(CONFIG["audio"]["sample_rate"]) * 2, dtype=np.float32)
        common = dict(language=CONFIG["transcription"]["dictation_language"],
                      beam_size=int(CONFIG["transcription"]["beam_size"]),
                      vad_filter=False, temperature=0.0, word_timestamps=True)
        if BATCHED is not None:
            segments, _ = BATCHED.transcribe(silence, batch_size=2,
                                             clip_timestamps=[{"start": 0, "end": 1}, {"start": 1, "end": 2}], **common)
        else:
            segments, _ = MODEL.transcribe(silence, **common)
        list(segments)
    except Exception as exc:
        log(f"[STARTUP] Warm-up skipped: {exc}")


# ---------------------------------------------------------------- post-processing (mirrors whisper-dictate.py)
def filter_hallucinations(segments):
    post_config = CONFIG["post_processing"]
    hard = set(post_config["hallucination_phrases"])
    soft = set(post_config.get("hallucination_phrases_low_confidence", []))
    threshold = float(post_config.get("hallucination_logprob_threshold", -1.0))
    patterns = [re.compile(p, re.IGNORECASE) for p in post_config.get("hallucination_patterns", [])]
    kept, dropped = [], []
    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        key = text.lower().rstrip(".!?,;:")
        if key in hard or any(p.search(text) for p in patterns):
            dropped.append({"text": text, "reason": "hallucination", "avg_logprob": seg.avg_logprob})
            continue
        if key in soft and seg.avg_logprob < threshold:
            dropped.append({"text": text, "reason": "low-confidence phrase", "avg_logprob": seg.avg_logprob})
            continue
        kept.append(seg)
    return kept, dropped


def apply_word_corrections(text):
    for pattern, replacement in CONFIG["post_processing"]["word_corrections"].items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def apply_spoken_punctuation(text):
    for pattern, replacement in CONFIG["post_processing"]["spoken_punctuation"].items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return re.sub(r"  +", " ", text).strip()


# ---------------------------------------------------------------- transcription worker
def transcribe_job(job):
    from faster_whisper.audio import decode_audio

    transcription_config = CONFIG["transcription"]
    options = job["options"]
    tmp_path = job.pop("tmp_path")
    try:
        audio = decode_audio(tmp_path, sampling_rate=int(CONFIG["audio"]["sample_rate"]))
    finally:
        try:
            os.remove(tmp_path)  # audio is not kept on disk
        except OSError:
            pass
    duration = len(audio) / int(CONFIG["audio"]["sample_rate"])
    if duration > ARGS.max_minutes * 60:
        raise ValueError(f"audio longer than {ARGS.max_minutes} minutes ({duration:.0f}s)")

    language = options.get("language") or transcription_config["dictation_language"]
    prompt = options.get("prompt")
    if prompt is None:
        prompt = str(transcription_config["initial_prompt"])
    word_timestamps = options.get("word_timestamps", True)
    common = dict(language=language, beam_size=int(transcription_config["beam_size"]),
                  vad_filter=bool(transcription_config["vad_filter"]),
                  condition_on_previous_text=bool(transcription_config["condition_on_previous_text"]),
                  initial_prompt=prompt or None, word_timestamps=word_timestamps)
    batch_size = ARGS.batch_size if ARGS.batch_size is not None else int(transcription_config.get("batch_size", 8))
    t0 = time.time()
    if BATCHED is not None and batch_size > 1 and common["vad_filter"]:
        segments, info = BATCHED.transcribe(audio, batch_size=batch_size, **common)
        mode = f"batch {batch_size}"
    else:
        segments, info = MODEL.transcribe(audio, **common)
        mode = "sequential"
    segments = list(segments)
    t_transcribe = time.time() - t0

    kept, dropped = filter_hallucinations(segments)
    text = " ".join(s.text.strip() for s in kept).strip()
    if options.get("spoken_punctuation", ARGS.spoken_punctuation):
        text = apply_spoken_punctuation(text)
    text = apply_word_corrections(text)

    seg_out = [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(),
                "avg_logprob": round(s.avg_logprob, 3), "no_speech_prob": round(s.no_speech_prob, 3)} for s in kept]
    words = []
    if word_timestamps:
        for s in kept:
            for w in (s.words or []):
                words.append({"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word.strip(),
                              "probability": round(w.probability, 3)})
    ratio = duration / t_transcribe if t_transcribe > 0 else 0.0
    log(f"[PERF] job {job['id']}: {duration:.1f}s audio -> {t_transcribe:.1f}s transcription ({ratio:.1f}x real-time, {mode})")
    return {
        "text": text, "segments": seg_out, "words": words, "dropped_segments": dropped,
        "language": info.language, "duration": round(duration, 2),
        "transcription_seconds": round(t_transcribe, 2), "realtime_factor": round(ratio, 1),
        "model": ARGS.model or str(CONFIG["model"]["size"]), "mode": mode,
    }


def worker():
    while True:
        job_id = JOB_QUEUE.get()
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job is None:
            continue
        job["status"] = "running"
        job["started"] = time.time()
        try:
            result = transcribe_job(job)
            job.update(result)
            job["status"] = "done"
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)
            log(f"[ERROR] job {job_id}: {exc}")
        job["finished"] = time.time()
        job["event"].set()


def expire_jobs():
    while True:
        time.sleep(60)
        cutoff = time.time() - ARGS.result_ttl * 60
        with JOBS_LOCK:
            for job_id in [j for j, job in JOBS.items() if job.get("finished", 0) and job["finished"] < cutoff]:
                del JOBS[job_id]


# ---------------------------------------------------------------- HTTP
def public_job(job):
    keys = ("id", "status", "created", "started", "finished", "error", "text", "segments", "words",
            "dropped_segments", "language", "duration", "transcription_seconds", "realtime_factor", "model", "mode")
    return {k: job[k] for k in keys if k in job}


def extract_audio(handler, body):
    """Return (bytes, filename) from multipart/form-data or a raw body."""
    ctype = handler.headers.get("Content-Type", "")
    if ctype.startswith("multipart/form-data"):
        msg = email.message_from_bytes(b"Content-Type: " + ctype.encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + body,
                                       policy=email.policy.HTTP)
        for part in msg.iter_parts():
            filename = part.get_filename()
            if filename or part.get_param("name", header="content-disposition") in ("audio", "file"):
                return part.get_payload(decode=True), filename or "upload"
        raise ValueError("multipart body without an audio part (use field name 'audio')")
    return body, "upload"


class Handler(BaseHTTPRequestHandler):
    server_version = "WhisperType/1.0"

    def log_message(self, fmt, *args):  # quieter default logging
        if ARGS.verbose:
            log(f"{self.address_string()} {fmt % args}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        if not ARGS.token:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {ARGS.token}"

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            with JOBS_LOCK:
                pending = sum(1 for j in JOBS.values() if j["status"] in ("queued", "running"))
            return self._json(200, {"status": "ok", "model": ARGS.model or CONFIG["model"]["size"],
                                    "device": ARGS.device or CONFIG["model"]["device"], "queue": pending})
        if not self._authorized():
            return self._json(401, {"error": "unauthorized"})
        m = re.fullmatch(r"/jobs/([A-Za-z0-9]+)", path)
        if m:
            with JOBS_LOCK:
                job = JOBS.get(m.group(1))
            if job is None:
                return self._json(404, {"error": "job not found"})
            return self._json(200, public_job(job))
        if path == "/jobs":
            with JOBS_LOCK:
                jobs = [{"id": j["id"], "status": j["status"], "created": j["created"]} for j in JOBS.values()]
            return self._json(200, {"jobs": jobs})
        self._json(404, {"error": "not found"})

    def do_DELETE(self):
        if not self._authorized():
            return self._json(401, {"error": "unauthorized"})
        m = re.fullmatch(r"/jobs/([A-Za-z0-9]+)", urlsplit(self.path).path)
        if not m:
            return self._json(404, {"error": "not found"})
        with JOBS_LOCK:
            existed = JOBS.pop(m.group(1), None) is not None
        self._json(200 if existed else 404, {"deleted": existed})

    def do_POST(self):
        parts = urlsplit(self.path)
        if parts.path not in ("/jobs", "/transcribe"):
            return self._json(404, {"error": "not found"})
        if not self._authorized():
            return self._json(401, {"error": "unauthorized"})
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return self._json(400, {"error": "empty body"})
        if length > ARGS.max_mb * 1024 * 1024:
            return self._json(413, {"error": f"body larger than {ARGS.max_mb} MB"})
        body = self.rfile.read(length)
        try:
            audio_bytes, filename = extract_audio(self, body)
        except Exception as exc:
            return self._json(400, {"error": str(exc)})
        if not audio_bytes:
            return self._json(400, {"error": "no audio data"})

        query = parse_qs(parts.query)
        options = {}
        if "language" in query:
            options["language"] = query["language"][0]
        if "prompt" in query:
            options["prompt"] = query["prompt"][0]
        if "word_timestamps" in query:
            options["word_timestamps"] = query["word_timestamps"][0] not in ("0", "false", "no")
        if "spoken_punctuation" in query:
            options["spoken_punctuation"] = query["spoken_punctuation"][0] in ("1", "true", "yes")

        suffix = os.path.splitext(filename)[1] or ".bin"
        fd, tmp_path = tempfile.mkstemp(prefix="whisper-upload-", suffix=suffix)
        with os.fdopen(fd, "wb") as f:
            f.write(audio_bytes)

        job_id = secrets.token_hex(6)
        job = {"id": job_id, "status": "queued", "created": time.time(), "tmp_path": tmp_path,
               "options": options, "event": threading.Event(), "bytes": len(audio_bytes), "filename": filename}
        with JOBS_LOCK:
            JOBS[job_id] = job
        JOB_QUEUE.put(job_id)
        log(f"[JOB] {job_id} queued: {filename} ({len(audio_bytes) / 1024:.0f} KB) from {self.client_address[0]}")

        if parts.path == "/transcribe":
            job["event"].wait()
            return self._json(200 if job["status"] == "done" else 500, public_job(job))
        self._json(202, {"job_id": job_id, "status": "queued", "poll": f"/jobs/{job_id}"})


def lan_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except socket.gaierror:
        pass
    return sorted(a for a in addresses if not a.startswith("127."))


def main():
    global ARGS, CONFIG
    parser = argparse.ArgumentParser(description="Whisper-Type local transcription server (LAN, development)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default=os.path.join(SCRIPT_DIR, "whisper-config.json"))
    parser.add_argument("--model", help="override model.size (e.g. small, large-v3-turbo, or a CTranslate2 repo/folder)")
    parser.add_argument("--device", help="override model.device (cuda|cpu)")
    parser.add_argument("--compute-type", dest="compute_type", help="override model.compute_type (float16, int8_float16, int8)")
    parser.add_argument("--threads", type=int, help="cpu_threads for device cpu")
    parser.add_argument("--batch-size", dest="batch_size", type=int, help="override transcription.batch_size (0/1 = sequential)")
    parser.add_argument("--token", help="require 'Authorization: Bearer <token>' on job endpoints")
    parser.add_argument("--max-mb", dest="max_mb", type=int, default=50, help="max upload size in MB (default 50)")
    parser.add_argument("--max-minutes", dest="max_minutes", type=int, default=20, help="max audio duration (default 20)")
    parser.add_argument("--result-ttl", dest="result_ttl", type=int, default=24 * 60, help="minutes to keep results (default 1440)")
    parser.add_argument("--spoken-punctuation", dest="spoken_punctuation", action="store_true",
                        help="apply post_processing.spoken_punctuation by default (dictation style)")
    parser.add_argument("--verbose", action="store_true", help="log every HTTP request")
    ARGS = parser.parse_args()

    CONFIG = load_config(ARGS.config)
    load_model()
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=expire_jobs, daemon=True).start()

    server = ThreadingHTTPServer((ARGS.host, ARGS.port), Handler)
    hosts = lan_addresses() if ARGS.host == "0.0.0.0" else [ARGS.host]
    for h in hosts:
        log(f"[STARTUP] Listening on http://{h}:{ARGS.port}  (health: /health, upload: POST /jobs, sync: POST /transcribe)")
    if not ARGS.token:
        log("[STARTUP] No token set: anyone in the LAN can upload audio. Use --token for anything beyond local testing.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
