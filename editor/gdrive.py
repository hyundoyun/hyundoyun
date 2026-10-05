"""구글 드라이브에서 영상 받기.

방법 1 (가장 쉬움): 드라이브에서 영상 '링크 복사'(링크가 있는 모든 사용자 보기) → 링크를 주면 받는다.
방법 2 (링크 없이 '올렸어'만 말해도 되게): 드라이브에 '편집할영상' 폴더를 만들고
        서비스 계정 키(GDRIVE_SERVICE_ACCOUNT_JSON 환경변수) 또는 rclone 설정(RCLONE_CONFIG_GDRIVE_*)을 넣어두면
        그 폴더에서 가장 최근에 올린 영상을 자동으로 받는다. 자세한 건 docs/구글드라이브_연결.md
"""
from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from .common import ROOT, log, run

VIDEO_EXT = (".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm")


def file_id(link: str) -> str | None:
    for pat in (r"/file/d/([\w-]+)", r"[?&]id=([\w-]+)", r"/open\?id=([\w-]+)"):
        m = re.search(pat, link)
        if m:
            return m.group(1)
    return link if re.fullmatch(r"[\w-]{20,}", link) else None


def download_link(link: str, dest: Path | None = None) -> Path:
    import gdown

    dest = dest or ROOT / "input"
    dest.mkdir(parents=True, exist_ok=True)
    fid = file_id(link)
    if "folders" in link:
        files = gdown.download_folder(link, output=str(dest), quiet=False) or []
        vids = [Path(f) for f in files if f.lower().endswith(VIDEO_EXT)]
        if not vids:
            raise SystemExit("폴더에 영상 파일이 없습니다.")
        return max(vids, key=lambda p: p.stat().st_mtime)
    out = gdown.download(id=fid, output=str(dest) + "/", quiet=False)
    if not out:
        raise SystemExit("다운로드 실패: 링크 공유 설정이 '링크가 있는 모든 사용자'인지 확인하세요.")
    return Path(out)


def _service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    if not info:
        return None
    creds = service_account.Credentials.from_service_account_info(
        json.loads(info), scopes=["https://www.googleapis.com/auth/drive"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def latest(folder_name: str = "편집할영상", dest: Path | None = None) -> Path:
    """'편집할영상' 폴더에서 가장 최근 영상 받기."""
    dest = dest or ROOT / "input"
    dest.mkdir(parents=True, exist_ok=True)
    svc = _service()
    if svc is not None:
        q = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        folders = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
        if not folders:
            raise SystemExit(f"드라이브에서 '{folder_name}' 폴더를 찾지 못했습니다(서비스 계정과 공유했는지 확인).")
        q = f"'{folders[0]['id']}' in parents and mimeType contains 'video/' and trashed = false"
        files = svc.files().list(q=q, orderBy="createdTime desc", pageSize=1,
                                 fields="files(id,name,createdTime)").execute().get("files", [])
        if not files:
            raise SystemExit(f"'{folder_name}' 폴더에 영상이 없습니다.")
        f = files[0]
        from googleapiclient.http import MediaIoBaseDownload

        out = dest / f["name"]
        with open(out, "wb") as fh:
            dl = MediaIoBaseDownload(fh, svc.files().get_media(fileId=f["id"]), chunksize=32 * 1024 * 1024)
            done = False
            while not done:
                st, done = dl.next_chunk()
                log(f"다운로드 {int(st.progress() * 100)}%")
        return out
    if shutil.which("rclone") and any(k.startswith("RCLONE_CONFIG_GDRIVE") for k in os.environ):
        r = run(["rclone", "lsjson", f"gdrive:{folder_name}", "--files-only"])
        vids = [f for f in json.loads(r.stdout) if f["Name"].lower().endswith(VIDEO_EXT)]
        if not vids:
            raise SystemExit(f"'{folder_name}' 폴더에 영상이 없습니다.")
        f = max(vids, key=lambda f: f["ModTime"])
        run(["rclone", "copy", f"gdrive:{folder_name}/{f['Name']}", str(dest)])
        return dest / f["Name"]
    raise SystemExit("NO_DRIVE_ACCESS: 구글 드라이브 연결 정보가 없습니다. docs/구글드라이브_연결.md 를 따라 연결하거나 영상 공유 링크를 주세요.")


def upload(path: Path, folder_name: str = "편집완료") -> str | None:
    """완성본을 드라이브 '편집완료' 폴더에 올린다(연결돼 있을 때만)."""
    svc = _service()
    if svc is not None:
        from googleapiclient.http import MediaFileUpload

        q = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
        folders = svc.files().list(q=q, fields="files(id)").execute().get("files", [])
        meta = {"name": path.name}
        if folders:
            meta["parents"] = [folders[0]["id"]]
        f = svc.files().create(body=meta, media_body=MediaFileUpload(str(path), resumable=True),
                               fields="id,webViewLink").execute()
        return f.get("webViewLink")
    if shutil.which("rclone") and any(k.startswith("RCLONE_CONFIG_GDRIVE") for k in os.environ):
        run(["rclone", "copy", str(path), f"gdrive:{folder_name}"])
        return f"gdrive:{folder_name}/{path.name}"
    return None
