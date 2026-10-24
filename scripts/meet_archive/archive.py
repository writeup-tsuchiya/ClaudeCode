#!/usr/bin/env python3
"""Google Meet 商談ログ（Gemini によるメモ）を少数のGoogleドキュメントに連結して集約する。

設計方針（2026-10-25 全面改訂）
---------------------------------
* 新規商談の「発見」はカレンダー起点を正とする。Gemini in Meet は保存先フォルダを
  予告なく変える（2026-09: Meet Recordings → Google Meet/<商談名>/、以後も作り直しが発生）ため、
  Driveのフォルダ構造に依存しない。
* 「読み取り」は次の順で試す。
    1. ドメイン全体の委任（MEET_ARCHIVE_IMPERSONATE=1）があれば各メンバーとして読む（恒久策）。
    2. なければサービスアカウント自身の権限で読む（各メンバーがフォルダを共有している範囲のみ）。
  読めなかったものは黙って捨てず manifest.lastRun.unreadable に残し、Slack報告に載せる。
* 状態（処理済みID・チャンク・プレースホルダ）はすべて config/meet-archive-manifest.json に持つ。
  スクラッチパッドにしか無いファイルを作らない（2026-09-30 の鍵消失事故で placeholders.json も失われた）。
* 認証鍵だけはリポジトリに置かない。環境変数 MEET_ARCHIVE_KEY でパスを渡す。

使い方
------
  # A. カレンダー起点（Claude が MCP で作った候補JSONを渡す）
  python archive.py --candidates candidates.json

  # B. Drive走査（共有済みファイルを全部拾う。バックフィルや取りこぼし回収用）
  python archive.py --scan-drive [--lookback-days N]   # N=0 で全期間

  # C. 委任モード（管理者設定後）。カレンダーもDriveも各メンバーとしてアクセスする
  MEET_ARCHIVE_IMPERSONATE=1 python archive.py --lookback-days 3

  A と B は同時指定可（候補を合算する）。--dry-run で書き込みなしに候補と可読性だけ確認できる。

出力
----
  標準エラーに進捗ログ、標準出力の最終行に JSON サマリ（Slack報告はこれから組み立てる）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

REPO_ROOT = Path(os.environ.get("MEET_ARCHIVE_REPO", Path(__file__).resolve().parents[2]))
CONFIG_PATH = REPO_ROOT / "config" / "meet-collector.json"
TEAM_PATH = REPO_ROOT / "config" / "team.json"

SCOPES_READ = ["https://www.googleapis.com/auth/drive.readonly",
               "https://www.googleapis.com/auth/calendar.readonly"]
SCOPES_WRITE = ["https://www.googleapis.com/auth/drive"]
DOC_MIME = "application/vnd.google-apps.document"
DOC_ID_RE = re.compile(r"/d/([A-Za-z0-9_-]{20,})")
GEMINI_SUFFIX_RE = re.compile(r"\s*-\s*Gemini によるメモ\s*$")


def log(msg: str) -> None:
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[{ts}] {msg}", file=sys.stderr, flush=True)


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- auth

class Auth:
    def __init__(self, key_path: Path, impersonate: bool):
        self.key_path = key_path
        self.impersonate = impersonate
        self._services: dict[tuple[str, str | None], object] = {}
        info = load_json(key_path)
        self.sa_email = info.get("client_email", "?")

    def _creds(self, scopes, subject: str | None):
        creds = service_account.Credentials.from_service_account_file(str(self.key_path), scopes=scopes)
        if subject:
            creds = creds.with_subject(subject)
        return creds

    def drive(self, as_user: str | None = None, write: bool = False):
        key = ("drive" + ("-w" if write else ""), as_user)
        if key not in self._services:
            scopes = SCOPES_WRITE if write else SCOPES_READ
            self._services[key] = build("drive", "v3", credentials=self._creds(scopes, as_user), cache_discovery=False)
        return self._services[key]

    def calendar(self, as_user: str):
        key = ("calendar", as_user)
        if key not in self._services:
            self._services[key] = build("calendar", "v3", credentials=self._creds(SCOPES_READ, as_user), cache_discovery=False)
        return self._services[key]


def retry(fn, what: str, tries: int = 4):
    delay = 2
    for i in range(tries):
        try:
            return fn()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in (403, 404) and "rateLimit" not in str(e) and "userRateLimit" not in str(e):
                raise
            if i == tries - 1:
                raise
            log(f"  retry {what}: HTTP {status} ({i + 1}/{tries}) sleeping {delay}s")
            time.sleep(delay)
            delay *= 2


# ---------------------------------------------------------------------- discovery

class Candidate:
    __slots__ = ("file_id", "member_name", "member_email", "event_title", "event_start", "source")

    def __init__(self, file_id, member_name, member_email, event_title="", event_start="", source="?"):
        self.file_id = file_id
        self.member_name = member_name
        self.member_email = member_email
        self.event_title = event_title or ""
        self.event_start = event_start or ""
        self.source = source

    def as_dict(self):
        return {"fileId": self.file_id, "member": self.member_name, "memberEmail": self.member_email,
                "eventTitle": self.event_title, "eventStart": self.event_start, "source": self.source}


def file_id_from(s: str) -> str | None:
    if not s:
        return None
    if "/" not in s:
        return s.strip()
    m = DOC_ID_RE.search(s)
    return m.group(1) if m else None


def member_lookup(cfg):
    by_email = {m["email"].lower(): m["name"] for m in cfg["members"]}
    return by_email


def is_excluded(title: str, team) -> bool:
    ex = team.get("exclude", {})
    t = (title or "").strip()
    if t in set(ex.get("titleExact", [])):
        return True
    return any(k in t for k in ex.get("titleContains", []))


def load_candidates_file(path: Path, by_email) -> list[Candidate]:
    rows = load_json(path)
    out = []
    for r in rows:
        fid = file_id_from(r.get("fileId") or r.get("fileUrl") or "")
        if not fid:
            continue
        email = (r.get("memberEmail") or r.get("organizer") or "").lower()
        name = r.get("member") or by_email.get(email, email.split("@")[0] if email else "不明")
        out.append(Candidate(fid, name, email, r.get("eventTitle", ""), r.get("eventStart", ""), "calendar"))
    return out


def discover_via_drive_scan(auth: Auth, cfg, lookback_days: int) -> list[Candidate]:
    target = cfg["targetFolder"]["id"]
    pattern = cfg["source"]["titlePattern"]
    out = []
    since = None
    if lookback_days > 0:
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for m in cfg["members"]:
        email, name = m["email"], m["name"]
        q = (f"'{email}' in owners and mimeType = '{DOC_MIME}' and name contains '{pattern}' "
             f"and not '{target}' in parents and trashed = false")
        if since:
            q += f" and createdTime > '{since}'"
        svc = auth.drive(as_user=email if auth.impersonate else None)
        page = None
        n = 0
        while True:
            resp = retry(lambda: svc.files().list(q=q, fields="nextPageToken, files(id,name,createdTime)",
                                                  pageSize=200, pageToken=page,
                                                  supportsAllDrives=True, includeItemsFromAllDrives=True).execute(),
                         f"drive.list {name}")
            for f in resp.get("files", []):
                out.append(Candidate(f["id"], name, email, GEMINI_SUFFIX_RE.sub("", f["name"]), f.get("createdTime", ""), "drive-scan"))
                n += 1
            page = resp.get("nextPageToken")
            if not page:
                break
        log(f"  {name}: {n} docs visible in Drive")
    return out


def discover_via_calendar_impersonated(auth: Auth, cfg, lookback_days: int) -> list[Candidate]:
    out = []
    now = dt.datetime.now(dt.timezone.utc)
    time_min = (now - dt.timedelta(days=max(lookback_days, 1))).isoformat()
    time_max = (now + dt.timedelta(hours=1)).isoformat()
    for m in cfg["members"]:
        email, name = m["email"], m["name"]
        svc = auth.calendar(as_user=email)
        page = None
        n = 0
        while True:
            resp = retry(lambda: svc.events().list(calendarId="primary", timeMin=time_min, timeMax=time_max,
                                                   singleEvents=True, orderBy="startTime", maxResults=250,
                                                   pageToken=page).execute(), f"calendar.list {name}")
            for ev in resp.get("items", []):
                for att in ev.get("attachments", []) or []:
                    if "Gemini によるメモ" not in (att.get("title") or ""):
                        continue
                    fid = file_id_from(att.get("fileUrl") or "") or att.get("fileId")
                    if not fid:
                        continue
                    start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date") or ""
                    out.append(Candidate(fid, name, email, ev.get("summary", ""), start, "calendar-impersonated"))
                    n += 1
            page = resp.get("nextPageToken")
            if not page:
                break
        log(f"  {name}: {n} Gemini memo attachments in calendar")
    return out


# ------------------------------------------------------------------------ reading

def read_doc(auth: Auth, cand: Candidate):
    """Returns (meta, text) or raises HttpError."""
    users = []
    if auth.impersonate and cand.member_email:
        users.append(cand.member_email)
    users.append(None)  # サービスアカウント自身
    last_err = None
    for u in users:
        svc = auth.drive(as_user=u)
        try:
            meta = retry(lambda: svc.files().get(fileId=cand.file_id, fields="id,name,createdTime,owners(emailAddress),mimeType",
                                                 supportsAllDrives=True).execute(), f"files.get {cand.file_id}")
            if meta.get("mimeType") != DOC_MIME:
                raise RuntimeError(f"not a Google Doc: {meta.get('mimeType')}")
            data = retry(lambda: svc.files().export(fileId=cand.file_id, mimeType="text/plain").execute(),
                         f"files.export {cand.file_id}")
            text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
            return meta, text.replace("\r\n", "\n").lstrip("﻿")
        except HttpError as e:
            last_err = e
            if getattr(e.resp, "status", None) in (403, 404):
                continue
            raise
    raise last_err  # type: ignore[misc]


def http_reason(e: Exception) -> str:
    if isinstance(e, HttpError):
        status = getattr(e.resp, "status", "?")
        return {404: "not found / 未共有", 403: "permission denied / 未共有"}.get(status, f"HTTP {status}")
    return str(e)[:120]


# ------------------------------------------------------------------------ writing

def build_entry(cfg, cand: Candidate, meta, text: str) -> str:
    sep = cfg["archive"]["separator"]
    name = GEMINI_SUFFIX_RE.sub("", meta.get("name", cand.event_title))
    header = "\n".join([
        sep,
        f"【担当】{cand.member_name}",
        f"【会議名】{name}",
        f"【日時】{meta.get('createdTime', cand.event_start)}",
        f"【元ファイル】https://docs.google.com/document/d/{meta['id']}/edit",
        sep,
    ])
    return header + "\n\n\n" + text.rstrip() + "\n\n\n"


def upload_chunk(auth: Auth, placeholder: dict, text: str) -> None:
    svc = auth.drive(write=True)
    media = MediaIoBaseUpload(io.BytesIO(text.encode("utf-8")), mimetype="text/plain", resumable=True)
    retry(lambda: svc.files().update(fileId=placeholder["fileId"], media_body=media, supportsAllDrives=True).execute(),
          f"files.update {placeholder['title']}")


def next_free_placeholder(manifest) -> dict | None:
    for p in manifest.get("placeholders", []):
        if p.get("status") == "free":
            return p
    return None


# ----------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", type=Path, help="カレンダーから抽出した候補JSON（Claude/MCPが作成）")
    ap.add_argument("--scan-drive", action="store_true", help="Driveをオーナー指定で走査して候補に加える")
    ap.add_argument("--lookback-days", type=int, default=None, help="走査/委任モードの遡り日数。0=全期間（Drive走査のみ）")
    ap.add_argument("--limit", type=int, default=int(os.environ.get("ARCHIVE_LIMIT", "0")), help="処理件数上限（テスト用）")
    ap.add_argument("--dry-run", action="store_true", help="書き込みせず、候補と可読性だけ確認")
    args = ap.parse_args()

    cfg = load_json(CONFIG_PATH)
    team = load_json(TEAM_PATH)
    manifest_path = REPO_ROOT / cfg["archive"]["manifest"]
    manifest = load_json(manifest_path)
    by_email = member_lookup(cfg)

    key_path = Path(os.environ.get(cfg["auth"]["keyEnv"], "secrets/service-account.json")).expanduser()
    if not key_path.exists():
        log(f"KEY MISSING: {key_path} が無い。{cfg['auth']['keyEnv']} を設定するか鍵を再発行する（docs/meet-archive-ops.md 参照）")
        print(json.dumps({"ok": False, "error": "KEY_MISSING", "keyPath": str(key_path)}, ensure_ascii=False))
        return 3
    impersonate = os.environ.get(cfg["auth"]["impersonateEnv"], "") in ("1", "true", "yes")
    auth = Auth(key_path, impersonate)
    log(f"auth: {auth.sa_email} impersonate={impersonate}")

    lookback = args.lookback_days if args.lookback_days is not None else int(cfg["discovery"]["lookbackDays"])

    # ---- discovery
    candidates: list[Candidate] = []
    if args.candidates:
        c = load_candidates_file(args.candidates, by_email)
        log(f"candidates file: {len(c)} rows")
        candidates += c
    if impersonate and not args.candidates:
        log("discovery: calendar (impersonated)")
        candidates += discover_via_calendar_impersonated(auth, cfg, lookback)
    if args.scan_drive:
        log(f"discovery: drive scan (lookback={lookback or 'all'})")
        candidates += discover_via_drive_scan(auth, cfg, lookback)
    if not candidates and not (args.candidates or args.scan_drive or impersonate):
        ap.error("--candidates か --scan-drive のどちらか（または委任モード）を指定してください")

    included = set(manifest.get("includedSourceFileIds", []))
    seen: dict[str, Candidate] = {}
    excluded = 0
    for c in candidates:
        if c.file_id in included or c.file_id in seen:
            continue
        if is_excluded(c.event_title, team):
            excluded += 1
            continue
        seen[c.file_id] = c
    todo = sorted(seen.values(), key=lambda c: c.event_start or "")
    log(f"discovered={len(candidates)} new={len(todo)} excluded_internal={excluded} already={len(included)}")
    if args.limit:
        todo = todo[: args.limit]

    # ---- read & archive
    processed: list[dict] = []
    unreadable: list[dict] = []
    chunks_written: list[dict] = []
    buffer: list[str] = []
    buffer_ids: list[str] = []
    buffer_chars = 0
    max_chars = int(cfg["archive"]["chunkMaxChars"])

    def flush() -> bool:
        nonlocal buffer, buffer_ids, buffer_chars
        if not buffer:
            return True
        ph = next_free_placeholder(manifest)
        if ph is None:
            log("PLACEHOLDERS EXHAUSTED: 集約フォルダに空のGoogleドキュメントを作り manifest.placeholders に追記してください")
            return False
        text = "".join(buffer)
        if args.dry_run:
            log(f"  [dry-run] would upload {len(text)} chars / {len(buffer_ids)} meetings -> {ph['title']}")
        else:
            log(f"  uploading {ph['title']} ({len(text)} chars, {len(buffer_ids)} meetings)...")
            upload_chunk(auth, ph, text)
            ph["status"] = "used"
            entry = {"title": ph["title"], "fileId": ph["fileId"],
                     "url": f"https://docs.google.com/document/d/{ph['fileId']}/edit",
                     "charCount": len(text), "meetingsIncluded": len(buffer_ids),
                     "addedAt": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            manifest.setdefault("chunks", []).append(entry)
            manifest.setdefault("includedSourceFileIds", []).extend(buffer_ids)
            manifest["totalMeetingsIncluded"] = len(manifest["includedSourceFileIds"])
            chunks_written.append(entry)
            save_json(manifest_path, manifest)
        buffer, buffer_ids, buffer_chars = [], [], 0
        return True

    stopped = False
    for i, c in enumerate(todo, 1):
        try:
            meta, text = read_doc(auth, c)
        except Exception as e:  # noqa: BLE001
            reason = http_reason(e)
            log(f"  [{i}/{len(todo)}] UNREADABLE {c.member_name} / {c.event_title[:40]} ({c.file_id}): {reason}")
            unreadable.append({**c.as_dict(), "reason": reason})
            continue
        entry = build_entry(cfg, c, meta, text)
        if buffer and buffer_chars + len(entry) > max_chars:
            if not flush():
                stopped = True
                break
        buffer.append(entry)
        buffer_ids.append(c.file_id)
        buffer_chars += len(entry)
        processed.append({**c.as_dict(), "chars": len(text), "createdTime": meta.get("createdTime", "")})
        if i % 10 == 0:
            log(f"  progress {i}/{len(todo)} buffer={buffer_chars} chars")
    if not stopped and not flush():
        stopped = True

    # ---- record last run (読めなかったものを残すのが目的)
    run = {
        "at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": {"candidatesFile": bool(args.candidates), "scanDrive": args.scan_drive, "impersonate": impersonate,
                 "lookbackDays": lookback, "dryRun": args.dry_run},
        "discovered": len(candidates), "new": len(todo), "excludedInternal": excluded,
        "processed": len(processed) if not args.dry_run else 0,
        "unreadable": unreadable,
        "chunksWritten": chunks_written,
        "placeholdersFree": sum(1 for p in manifest.get("placeholders", []) if p.get("status") == "free"),
        "stoppedPlaceholdersExhausted": stopped,
    }
    if not args.dry_run:
        manifest["lastRun"] = run
        save_json(manifest_path, manifest)

    summary = {"ok": not stopped, **{k: v for k, v in run.items() if k != "unreadable"},
               "unreadableCount": len(unreadable),
               "unreadableByMember": _count_by(unreadable, "member"),
               "unreadable": unreadable[:50],
               "totalIncluded": manifest.get("totalMeetingsIncluded"),
               "chunkCount": len(manifest.get("chunks", []))}
    log(f"RUN COMPLETE processed={summary['processed']} unreadable={len(unreadable)} "
        f"chunks+={len(chunks_written)} total={summary['totalIncluded']} placeholders_free={run['placeholdersFree']}")
    print(json.dumps(summary, ensure_ascii=False))
    return 2 if stopped else 0


def _count_by(rows, key):
    out: dict[str, int] = {}
    for r in rows:
        out[r.get(key, "?")] = out.get(r.get(key, "?"), 0) + 1
    return out


if __name__ == "__main__":
    sys.exit(main())
