#!/usr/bin/env python3
"""
pr-scan.py - read-only security pre-scan for pull requests (whisper-type).

Usage:
    python .claude/skills/pr-review/pr-scan.py <PR-number> [--repo OWNER/NAME] [--de-branch deutsch] [--diff]

What it does (nothing from the PR is executed, installed or checked out):
  1. GitHub API: PR metadata, author + fork profile, commits, changed files, comments, reviews
  2. git fetch of the PR head into local branch pr-<N> (fetch only, never checkout)
  3. Scan of ADDED diff lines: dangerous code patterns, URLs/links, hidden unicode,
     obfuscated blobs, prompt-injection phrases (EN + DE), HTML/CSS hiding tricks
  4. Same text scans on PR title/body, commit messages, comments and reviews
  5. Cross-checks: API file list vs. local git, symlinks/mode changes, compile-only syntax
     check of changed .py files, hidden unicode per changed file (new vs. pre-existing),
     merge dry-run into the base branch and the German branch, whitespace check
  6. Tagged report [HIGH] / [MED] / [INFO] + summary. The verdict stays with the reviewer.

Optional: GITHUB_TOKEN env var raises the API rate limit (read-only use). Stdlib only.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

API = "https://api.github.com"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

HIGH, MED, INFO = "[HIGH]", "[MED] ", "[INFO]"
COUNT = {"HIGH": 0, "MED": 0}


def flag(level, msg):
    if level == HIGH:
        COUNT["HIGH"] += 1
    elif level == MED:
        COUNT["MED"] += 1
    print(f"  {level} {msg}")


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def snippet(text, n=170):
    text = text.replace("\n", "\\n")
    return text if len(text) <= n else text[:n] + " ..."


# --------------------------------------------------------------------------- helpers
def gh(path, accept="application/vnd.github+json", raw=False):
    req = urllib.request.Request(API + path, headers={"Accept": accept, "User-Agent": "whisper-type-pr-scan"})
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        print(f"  API error {e.code} for {path}")
        return "" if raw else None
    except Exception as e:  # network down etc.
        print(f"  API failure for {path}: {e}")
        return "" if raw else None
    return data if raw else json.loads(data)


def gh_paged(path):
    out, page = [], 1
    while True:
        sep = "&" if "?" in path else "?"
        chunk = gh(f"{path}{sep}per_page=100&page={page}")
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return out


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def days_since(iso):
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def detect_repo():
    url = git("remote", "get-url", "origin").stdout.strip()
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else None


# --------------------------------------------------------------------------- rule sets
FILE_RULES = [
    (HIGH, "CI/automation (runs on GitHub with repo permissions)", re.compile(r"^\.github/|\.ya?ml$", re.I)),
    (HIGH, "script/installer (runs on user PCs)", re.compile(r"\.(bat|cmd|ps1|psm1|sh|vbs|js|wsf|reg|lnk)$", re.I)),
    (HIGH, "binary/unreviewable by diff", re.compile(
        r"\.(exe|dll|pyd|so|dylib|zip|7z|rar|gz|tar|bin|pt|pth|onnx|gif|png|jpe?g|webp|ico|pdf|wav|mp3)$", re.I)),
    (MED, "AI-read text (prompt-injection surface)", re.compile(
        r"(^|/)(CLAUDE\.md|README[^/]*|AGENTS\.md|\.cursorrules|\.clinerules|copilot-instructions\.md)$|^\.claude/|^\.cursor/", re.I)),
    (MED, "config / dependency / git-behaviour file", re.compile(
        r"(^|/)(whisper-config\.json|requirements[^/]*\.txt|pyproject\.toml|setup\.(py|cfg)|Pipfile[^/]*|poetry\.lock|package\.json|\.gitattributes|\.gitignore|\.gitmodules)$", re.I)),
    (INFO, "python source", re.compile(r"\.py$", re.I)),
    (INFO, "markdown/doc", re.compile(r"\.(md|txt|rst)$", re.I)),
]

CODE_PATTERNS = [
    (HIGH, "exec/eval", re.compile(r"\b(exec|eval)\s*\(|\bcompile\s*\([^)]*['\"]exec['\"]")),
    (HIGH, "dynamic import", re.compile(r"__import__\s*\(|importlib\.import_module\s*\(")),
    (HIGH, "obfuscation/decoding", re.compile(
        r"b64decode|base64\.|codecs\.decode|zlib\.decompress|marshal\.loads|pickle\.loads?|bytes\.fromhex|(\\x[0-9a-fA-F]{2}){3,}|rot13|chr\(\d+\)\s*\+")),
    (HIGH, "network/outbound", re.compile(
        r"\bsocket\.|urllib\.request|urlopen\(|requests\.(get|post|put|patch|delete|Session)|http\.client|httpx\.|aiohttp|ftplib|smtplib|websocket|telnetlib|paramiko|webbrowser\.open")),
    (HIGH, "keylogging/input capture", re.compile(
        r"keyboard\.(hook|on_press|on_release|record|start_recording|read_event|read_key|get_typed_strings)|pynput|GetAsyncKeyState|SetWindowsHookEx|keylog", re.I)),
    (HIGH, "registry/autostart/persistence", re.compile(
        r"winreg|HKEY_|CurrentVersion\\+Run|schtasks|\breg\s+add|StartupApproved|shell:startup|Start Menu\\+Programs\\+Startup", re.I)),
    (HIGH, "deletion/destructive", re.compile(
        r"shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|\brmdir\s+/s|\bdel\s+/[fqs]|Remove-Item|\brd\s+/s|\brm\s+-rf|\bformat\s+[a-z]:", re.I)),
    (HIGH, "shell download/execute tricks", re.compile(
        r"powershell|pwsh|-EncodedCommand|\s-e(nc|c)?\s+[A-Za-z0-9+/=]{20,}|Invoke-WebRequest|\biwr\b|Invoke-Expression|\biex\b|DownloadString|DownloadFile|certutil|bitsadmin|mshta|rundll32|wscript|cscript|Start-Process[^\n]*Hidden|WindowStyle\s+Hidden|\bcurl\s+-|\bcurl\s+http|\bwget\s+|Invoke-RestMethod", re.I)),
    (HIGH, "package install / index change (supply chain)", re.compile(
        r"pip3?\s+install|--index-url|--extra-index-url|--trusted-host|--find-links|easy_install|\bnpm\s+i(nstall)?\b|\bnpx\s|\bchoco\s+install|\bwinget\s+install|git\+https?://", re.I)),
    (MED, "subprocess/os.system", re.compile(r"subprocess\.|os\.system|os\.popen|os\.exec\w*\(|os\.spawn\w*\(|Popen\(")),
    (MED, "native/ctypes", re.compile(r"\bctypes\b|win32api|win32con|win32gui|pywin32|\bcffi\b")),
    (MED, "env/credential access", re.compile(
        r"os\.environ|getenv\(|\.env\b|credential|passw(or)?d|\btoken\b|secret|keyring|api[_-]?key|HF_TOKEN|HUGGING_FACE", re.I)),
    (MED, "clipboard/history/dictated-text access", re.compile(
        r"pyperclip\.(paste|copy)|whisper-history\.log|history_path|HISTORY_PATH|clipboard", re.I)),
    (MED, "model source / HF download", re.compile(
        r"huggingface|hf_hub|snapshot_download|trust_remote_code|download_root|cache_dir|local_files_only|use_auth_token", re.I)),
    (MED, "microphone/audio stream", re.compile(r"InputStream\(|sounddevice\.|\bsd\.rec\b|pyaudio", re.I)),
    (MED, "screen/webcam capture", re.compile(r"ImageGrab|pyautogui|\bmss\b|VideoCapture|screenshot", re.I)),
    (MED, "file write outside project (abs/home/temp/appdata)", re.compile(
        r"open\(\s*['\"](/|[A-Za-z]:\\|~)|expanduser\(|APPDATA|LOCALAPPDATA|\bTEMP\b|gettempdir", re.I)),
    (MED, "hotkey / keyboard wait change", re.compile(r"\"hotkeys\"|add_hotkey\(|keyboard\.wait\(", re.I)),
]

INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|the\s+|any\s+|your\s+)?(previous|prior|above|earlier|preceding|system)\s+(instructions?|rules?|prompts?|guidelines?)",
    r"disregard\s+(all\s+|the\s+|your\s+|any\s+)?(previous|prior|above|earlier|system)",
    r"forget\s+(all\s+|the\s+|your\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?)",
    r"ignorier\w*\s+(alle\s+|die\s+|deine\s+)?(vorherigen|bisherigen|obigen|fr[üu]heren)\s+(anweisungen|regeln|instruktionen)",
    r"\byou\s+are\s+(now\s+)?(an?\s+)?(ai|assistant|claude|chatgpt|gpt|llm|language\s+model)\b",
    r"\bdu\s+bist\s+(jetzt\s+)?(ein|eine)\s+(ki|ai|assistent)",
    r"\bas\s+an\s+ai\b",
    r"\bsystem\s*prompt\b",
    r"<\s*/?\s*(system|assistant|human|instructions?)\s*>",
    r"\[\s*(system|inst)\s*\]",
    r"<\|(im_start|im_end|endoftext|system|user|assistant)\|>",
    r"^\s*#{2,}\s*(system|instruction)s?\b",
    r"^\s*(assistant|human|system)\s*:\s",
    r"\b(claude|copilot|cursor|chatgpt|gpt|llm|ai\s+agent|ai\s+reviewer|code\s+reviewer|automated\s+reviewer|language\s+model)s?\b[^.\n]{0,80}\b(must|should|shall|need\s+to|have\s+to|approve|merge|ignore|run|execute|skip|trust|always|never)\b",
    r"\b(approve|merge|accept)\s+(this|the)\s+(pr|pull\s+request|change)\b",
    r"\b(do\s+not|don't|never)\s+(flag|report|mention|review|warn|question)\b",
    r"\bthis\s+(code|change|file|pr)\s+(is|has\s+been|was)\s+(safe|secure|reviewed|audited|approved|verified)\b",
    r"\brun\s+(the\s+following|this|these)\s+(command|script|code)s?\b",
    r"\bnew\s+instructions?\b",
    r"\bnote\s+to\s+(the\s+)?(ai|assistant|reviewer|claude|bot)\b",
    r"\bfor\s+(the\s+)?(ai|llm|assistant)s?\s*(only|:)",
    r"\bdeveloper\s+mode\b",
    r"\bjailbreak\b",
]
INJECTION_RES = [re.compile(p, re.I | re.M) for p in INJECTION_PATTERNS]
HTML_COMMENT = re.compile(r"<!--(.*?)-->", re.S)
HIDDEN_CSS = re.compile(
    r"color\s*:\s*(white|#fff(fff)?|transparent)|display\s*:\s*none|font-size\s*:\s*0|visibility\s*:\s*hidden|opacity\s*:\s*0\b", re.I)
HTML_IN_MD = re.compile(r"<\s*(a|img|script|iframe|svg|object|embed|link|meta|style|form|input)\b", re.I)

URL_RE = re.compile(r"(?i)\b((?:https?|ftp|file)://[^\s'\"<>)\]]+|www\.[a-z0-9.-]+\.[a-z]{2,}[^\s'\"<>)\]]*)")
MD_LINK = re.compile(r"!?\[([^\]]*)\]\(([^)\s]+)[^)]*\)")
ALLOWED_HOSTS = {
    "github.com", "raw.githubusercontent.com", "objects.githubusercontent.com", "user-images.githubusercontent.com",
    "huggingface.co", "pypi.org", "python.org", "docs.python.org", "developer.nvidia.com", "nvidia.com",
    "docs.nvidia.com", "microsoft.com", "learn.microsoft.com", "shields.io", "img.shields.io", "opensource.org",
    "creativecommons.org", "pytorch.org", "opennmt.net", "openai.com", "ctranslate2.readthedocs.io",
}
SHORTENERS = {"bit.ly", "t.co", "tinyurl.com", "goo.gl", "is.gd", "cutt.ly", "rb.gy", "ow.ly", "shorturl.at",
              "rebrand.ly", "buff.ly", "t.ly", "tiny.cc", "lnkd.in", "tr.im", "v.gd"}

BIDI = {0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069, 0x061C, 0x200E, 0x200F}
INVISIBLE = {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF, 0x00AD, 0x180E, 0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0,
             0x2028, 0x2029, 0x00A0}
MIXED_SCRIPT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*[Ѐ-ӿͰ-Ͽ]|[Ѐ-ӿͰ-Ͽ][A-Za-z0-9_]*[A-Za-z]")
BASE64_BLOB = re.compile(r"[A-Za-z0-9+/]{60,}={0,2}")
HEX_BLOB = re.compile(r"(?:0x[0-9a-fA-F]{2}\s*,\s*){16,}|(?:[0-9a-fA-F]{2}){40,}")


# --------------------------------------------------------------------------- scanners
def scan_unicode(text, where, baseline_text=None):
    """Report bidi / invisible / mixed-script characters. Returns count of suspicious chars."""
    def collect(t):
        hits = []
        for i, ch in enumerate(t):
            cp = ord(ch)
            if cp in BIDI or cp in INVISIBLE or (cp > 127 and unicodedata.category(ch) in ("Cf", "Co", "Cn")):
                hits.append((i, cp))
        return hits

    hits = collect(text)
    base_hits = collect(baseline_text) if baseline_text is not None else []
    if hits and baseline_text is not None and len(hits) <= len(base_hits) and hits[:3] == base_hits[:3]:
        print(f"  {INFO} {where}: {len(hits)} hidden/format char(s) already present on base branch "
              f"(e.g. U+{hits[0][1]:04X} {unicodedata.name(chr(hits[0][1]), '?')}), not introduced by PR")
    elif hits:
        names = ", ".join(f"U+{cp:04X} {unicodedata.name(chr(cp), '?')} @{i}" for i, cp in hits[:6])
        level = HIGH if any(cp in BIDI or cp in INVISIBLE - {0x00A0, 0xFEFF} for _, cp in hits) else MED
        flag(level, f"{where}: {len(hits)} hidden/format/bidi char(s): {names}")
    for m in MIXED_SCRIPT.finditer(text):
        flag(HIGH, f"{where}: mixed-script identifier (homoglyph?) {snippet(m.group(0), 40)!r}")
    return len(hits)


def check_url(url, where, pr_user=None, repo=None):
    u = url if "://" in url else "http://" + url
    try:
        parts = urllib.parse.urlsplit(u)
        host = (parts.hostname or "").lower()
        path = parts.path
    except Exception:
        host, path = "", ""
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        flag(HIGH, f"{where}: raw IP address URL {url}")
    elif host in SHORTENERS or any(host.endswith("." + s) for s in SHORTENERS):
        flag(HIGH, f"{where}: URL shortener (destination hidden) {url}")
    elif host.startswith("xn--") or ".xn--" in host:
        flag(HIGH, f"{where}: punycode/IDN host (homoglyph domain?) {url}")
    elif u.lower().startswith("http://") and host not in ("localhost", "127.0.0.1"):
        flag(MED, f"{where}: plain http:// link {url}")
    elif host == "github.com":
        own = bool(repo) and path.lower().startswith("/" + repo.lower())
        fork = bool(pr_user) and path.lower().startswith("/" + pr_user.lower() + "/")
        if own:
            print(f"  {INFO} {where}: link to this repo {url}")
        elif fork:
            print(f"  {INFO} {where}: link to PR author's GitHub {url}")
        else:
            flag(MED, f"{where}: link to THIRD-PARTY GitHub path {url} (check what it is)")
    elif host in ALLOWED_HOSTS or any(host.endswith("." + a) for a in ALLOWED_HOSTS):
        print(f"  {INFO} {where}: known host {url}")
    else:
        flag(MED, f"{where}: UNKNOWN external host {url} (Fremdverlinkung, verify manually)")


def scan_text(text, where, pr_user=None, repo=None, is_markdown=False):
    """Prompt-injection, links, HTML hiding tricks and hidden unicode in free text."""
    if not text:
        return
    for rx in INJECTION_RES:
        for m in rx.finditer(text):
            flag(HIGH, f"{where}: prompt-injection phrase {snippet(m.group(0), 100)!r}")
    for m in HTML_COMMENT.finditer(text):
        body = m.group(1).strip()
        flag(MED, f"{where}: HTML comment (invisible when rendered): {snippet(body, 140)!r}")
    for m in HIDDEN_CSS.finditer(text):
        flag(HIGH, f"{where}: CSS hiding trick {m.group(0)!r}")
    if is_markdown:
        for m in HTML_IN_MD.finditer(text):
            flag(MED, f"{where}: raw HTML tag <{m.group(1)}> in markdown")
    for m in MD_LINK.finditer(text):
        label, target = m.group(1), m.group(2)
        is_img = text[m.start()] == "!"
        lab_host = re.search(r"(?i)\b([a-z0-9.-]+\.[a-z]{2,})\b", label)
        tgt_host = (urllib.parse.urlsplit(target if "://" in target else "http://" + target).hostname or "").lower()
        if lab_host and tgt_host and lab_host.group(1).lower().lstrip("www.") != tgt_host.lstrip("www."):
            flag(HIGH, f"{where}: link text '{label}' does not match target host '{tgt_host}' ({target})")
        if is_img and tgt_host and not (tgt_host in ALLOWED_HOSTS or any(tgt_host.endswith("." + a) for a in ALLOWED_HOSTS)):
            flag(MED, f"{where}: external image {target} (tracking pixel / content swap risk)")
    seen = set()
    for m in URL_RE.finditer(text):
        url = m.group(1).rstrip(".,;:")
        if url in seen:
            continue
        seen.add(url)
        check_url(url, where, pr_user, repo)
    scan_unicode(text, where)


def scan_diff(diff_text, pr_user=None, repo=None):
    """Scan ADDED lines of a unified diff."""
    current, added_total = None, 0
    per_file_added = {}
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            current = raw[6:] if raw.startswith("+++ b/") else raw[4:]
            continue
        if raw.startswith("---") or not raw.startswith("+"):
            continue
        line = raw[1:]
        added_total += 1
        per_file_added.setdefault(current, []).append(line)
        where = f"{current}"
        if len(line) > 400:
            flag(MED, f"{where}: very long line ({len(line)} chars) {snippet(line, 80)!r}")
        for level, label, rx in CODE_PATTERNS:
            m = rx.search(line)
            if m:
                flag(level, f"{where}: {label}: {snippet(line.strip(), 150)!r}")
        for m in BASE64_BLOB.finditer(line):
            flag(MED, f"{where}: base64-like blob ({len(m.group(0))} chars) {snippet(m.group(0), 50)!r}")
        if HEX_BLOB.search(line):
            flag(HIGH, f"{where}: hex blob (embedded binary/shellcode?) {snippet(line.strip(), 80)!r}")
    for fname, lines in per_file_added.items():
        is_md = bool(fname) and fname.lower().endswith((".md", ".txt", ".rst"))
        scan_text("\n".join(lines), f"{fname} (added lines)", pr_user, repo, is_markdown=is_md)
    return added_total


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="Read-only PR security pre-scan")
    ap.add_argument("number", type=int)
    ap.add_argument("--repo", default=None, help="owner/name (default: from git remote origin)")
    ap.add_argument("--de-branch", default="deutsch")
    ap.add_argument("--diff", action="store_true", help="print the full diff at the end")
    args = ap.parse_args()
    n = args.number
    repo = args.repo or detect_repo()
    if not repo:
        print("Cannot detect GitHub repo from origin remote; pass --repo owner/name")
        return 2

    section(f"PR #{n} on {repo} - METADATA")
    pr = gh(f"/repos/{repo}/pulls/{n}")
    if not pr or "number" not in pr:
        print("PR not found / API unavailable")
        return 2
    user = pr["user"]["login"]
    base = pr["base"]["ref"]
    meta = gh(f"/repos/{repo}") or {}
    if meta.get("homepage"):  # the upstream repo's own homepage is not a foreign link
        hp = (urllib.parse.urlsplit(meta["homepage"]).hostname or "").lower()
        if hp:
            ALLOWED_HOSTS.update({hp, hp[4:] if hp.startswith("www.") else "www." + hp})
    print(f"  title      : {pr['title']}")
    print(f"  state      : {pr['state']}  merged_at={pr.get('merged_at')}  draft={pr.get('draft')}")
    print(f"  author     : {user} ({pr['user']['type']})")
    print(f"  head -> base: {pr['head']['label']} ({pr['head']['sha'][:10]}) -> {base}")
    print(f"  created    : {pr['created_at']}   updated: {pr['updated_at']}")
    print(f"  commits={pr.get('commits')} files={pr.get('changed_files')} +{pr.get('additions')} -{pr.get('deletions')}")
    print(f"  mergeable  : {pr.get('mergeable')} ({pr.get('mergeable_state')})")
    print(f"  url        : {pr['html_url']}")
    if base != "master":
        flag(MED, f"PR targets '{base}' instead of master")
    if pr["head"].get("repo") and pr["head"]["repo"].get("full_name", "").lower() == repo.lower():
        print(f"  {INFO} PR comes from a branch inside this repo (collaborator)")
    body = pr.get("body") or ""
    print("  --- body ---")
    print("  " + (body.replace("\n", "\n  ") if body else "(empty)"))
    scan_text(pr.get("title", ""), "PR title", user, repo)
    scan_text(body, "PR body", user, repo, is_markdown=True)

    section("AUTHOR + FORK")
    u = gh(f"/users/{user}") or {}
    age = days_since(u.get("created_at", ""))
    print(f"  login={u.get('login')} name={u.get('name')} type={u.get('type')} company={u.get('company')} "
          f"location={u.get('location')} blog={u.get('blog')}")
    print(f"  account age: {age} days  public_repos={u.get('public_repos')} followers={u.get('followers')} "
          f"following={u.get('following')}")
    if u.get("blog"):
        check_url(u["blog"], "author blog", user, repo)
    if age is not None and age < 90:
        flag(MED, f"author account is only {age} days old")
    if (u.get("public_repos") or 0) == 0 and (u.get("followers") or 0) == 0:
        flag(MED, "author has no public repos and no followers (throwaway account?)")
    head_repo = pr["head"].get("repo") or {}
    if head_repo:
        fr = gh(f"/repos/{head_repo['full_name']}") or {}
        f_age = days_since(fr.get("created_at", ""))
        print(f"  fork: {fr.get('full_name')} created {fr.get('created_at')} ({f_age} days ago) pushed {fr.get('pushed_at')} "
              f"parent={(fr.get('parent') or {}).get('full_name')}")
        if fr.get("homepage"):
            check_url(fr["homepage"], "fork homepage (inherited from upstream unless changed)", user, repo)
    else:
        flag(MED, "head repository deleted / unavailable")

    section("COMMITS")
    commits = gh_paged(f"/repos/{repo}/pulls/{n}/commits")
    for c in commits:
        cm = c["commit"]
        a, co = cm["author"], cm["committer"]
        ver = cm.get("verification", {})
        gh_author = (c.get("author") or {}).get("login")
        print(f"  {c['sha'][:10]} {a['date']} author={a['name']} <{a['email']}> committer={co['name']} <{co['email']}> "
              f"gh_login={gh_author} signed={ver.get('verified')} ({ver.get('reason')})")
        print(f"      msg: {snippet(cm['message'], 200)!r}")
        if gh_author and gh_author.lower() != user.lower():
            flag(MED, f"commit {c['sha'][:10]} authored by GitHub user '{gh_author}', PR opened by '{user}'")
        if a["email"].lower() != co["email"].lower() and "noreply@github.com" not in co["email"]:
            flag(MED, f"commit {c['sha'][:10]}: author email != committer email")
        scan_text(cm["message"], f"commit {c['sha'][:10]} message", user, repo)
    if len(commits) > 15:
        flag(MED, f"{len(commits)} commits: review in chunks, check for 'fix' commits that revert earlier safe code")

    section("CHANGED FILES (API)")
    files = gh_paged(f"/repos/{repo}/pulls/{n}/files")
    api_names = set()
    for f in files:
        name = f["filename"]
        api_names.add(name)
        line = f"{name} [{f['status']}] +{f['additions']} -{f['deletions']}"
        if f.get("previous_filename"):
            line += f" (renamed from {f['previous_filename']})"
        matched = False
        for level, label, rx in FILE_RULES:
            if rx.search(name):
                matched = True
                if level in (HIGH, MED):
                    flag(level, f"{line}: {label}")
                else:
                    print(f"  {INFO} {line}: {label}")
                break
        if not matched:
            flag(MED, f"{line}: unclassified file type")
        if f["status"] == "removed":
            flag(MED, f"{name}: file DELETED by PR")
    if not files:
        flag(MED, "API returned no changed files (binary-only / huge PR / API limit?)")

    section("COMMENTS + REVIEWS")
    for kind, path in (("issue comment", f"/repos/{repo}/issues/{n}/comments"),
                       ("review comment", f"/repos/{repo}/pulls/{n}/comments"),
                       ("review", f"/repos/{repo}/pulls/{n}/reviews")):
        items = gh_paged(path)
        print(f"  {kind}s: {len(items)}")
        for it in items:
            who = it["user"]["login"]
            when = it.get("created_at") or it.get("submitted_at")
            txt = it.get("body") or ""
            print(f"    - {who} {when} {it.get('state', '')}: {snippet(txt, 160)!r}")
            scan_text(txt, f"{kind} by {who}", user, repo, is_markdown=True)

    section("LOCAL GIT (fetch only, no checkout)")
    fe = git("fetch", "origin", f"+refs/pull/{n}/head:refs/heads/pr-{n}")
    print("  " + (fe.stderr.strip().replace("\n", "\n  ") or fe.stdout.strip() or "fetched"))
    head_local = git("rev-parse", f"pr-{n}").stdout.strip()
    if head_local != pr["head"]["sha"]:
        flag(HIGH, f"local pr-{n} ({head_local[:10]}) != API head sha ({pr['head']['sha'][:10]}): force-pushed meanwhile? re-run")
    git("fetch", "origin", base)
    local_base = git("rev-parse", base).stdout.strip()
    remote_base = git("rev-parse", f"origin/{base}").stdout.strip()
    if local_base != remote_base:
        flag(MED, f"local {base} ({local_base[:7]}) != origin/{base} ({remote_base[:7]}): pull {base} before merging")
    mb = git("merge-base", base, f"pr-{n}").stdout.strip()
    counts = git("rev-list", "--left-right", "--count", f"{base}...pr-{n}").stdout.split()
    print(f"  merge-base {mb[:10]}  {base} ahead {counts[0] if counts else '?'} / PR ahead {counts[1] if len(counts) > 1 else '?'}")
    ns = git("diff", f"{base}...pr-{n}", "--name-status").stdout.strip()
    print("  name-status:\n    " + (ns.replace("\n", "\n    ") or "(none)"))
    local_names = {l.split("\t")[-1] for l in ns.splitlines() if l.strip()}
    if api_names != local_names:
        flag(HIGH, f"API file list != local git file list. API only: {sorted(api_names - local_names)} "
                   f"git only: {sorted(local_names - api_names)}")
    summ = git("diff", f"{base}...pr-{n}", "--summary").stdout
    for l in summ.splitlines():
        if "120000" in l:
            flag(HIGH, f"symlink introduced: {l.strip()}")
        elif "mode change" in l or "create mode 100755" in l:
            flag(MED, f"file mode change: {l.strip()}")
    for l in git("ls-tree", "-r", f"pr-{n}").stdout.splitlines():
        if l.startswith("120000"):
            flag(HIGH, f"symlink in PR tree: {l.split()[-1]}")
    print("  " + git("diff", f"{base}...pr-{n}", "--shortstat").stdout.strip())
    chk = git("diff", f"{base}...pr-{n}", "--check")
    if chk.stdout.strip():
        print(f"  {INFO} whitespace issues:\n    " + chk.stdout.strip().replace("\n", "\n    "))
    for fname in sorted(local_names):
        src = git("show", f"pr-{n}:{fname}")
        if src.returncode != 0:
            continue  # deleted by PR
        if fname.lower().endswith(".py"):
            try:
                compile(src.stdout, fname, "exec")  # parse only, never executed
                print(f"  {INFO} {fname}: compiles (syntax ok)")
            except SyntaxError as e:
                flag(HIGH, f"{fname}: SyntaxError line {e.lineno}: {e.msg}")
        if "\x00" not in src.stdout[:4000]:
            basesrc = git("show", f"{base}:{fname}")
            scan_unicode(src.stdout, f"{fname} (full file)", basesrc.stdout if basesrc.returncode == 0 else None)
    for branch in (base, args.de_branch):
        if git("rev-parse", "--verify", "-q", branch).returncode != 0:
            print(f"  {INFO} branch {branch} not found locally, merge dry-run skipped")
            continue
        mt = git("merge-tree", "--write-tree", branch, f"pr-{n}")
        if mt.returncode == 0:
            print(f"  {INFO} merge dry-run into {branch}: CLEAN")
        elif mt.returncode == 1:
            flag(MED, f"merge dry-run into {branch}: CONFLICTS\n    " + mt.stdout.strip().replace("\n", "\n    ")[:600])
        else:
            print(f"  {INFO} merge-tree unavailable for {branch} (git >= 2.38 needed)")

    section("DIFF SCAN (added lines only)")
    diff_text = git("diff", f"{base}...pr-{n}").stdout
    added = scan_diff(diff_text, user, repo)
    print(f"  scanned {added} added lines in {len(local_names)} file(s)")
    if args.diff:
        section("FULL DIFF")
        print(diff_text)

    section("SUMMARY")
    print(f"  HIGH findings: {COUNT['HIGH']}   MED findings: {COUNT['MED']}")
    print(f"  Next: read the complete diff yourself:  git diff {base}...pr-{n}")
    print(f"  Rules: verdict is reviewer judgement, not this count. Never checkout or run pr-{n} before the verdict.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
