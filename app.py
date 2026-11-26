import json
import os
import re
import unicodedata
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, request, send_from_directory
from google.oauth2 import service_account
from googleapiclient.discovery import build

VN_TZ = timezone(timedelta(hours=7))


def now_vn():
    return datetime.now(VN_TZ)


env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

app = Flask(__name__)

DEFAULT_TOKEN = (
    "QJFui9vie8HNdqt8qdvqTIzpotE5h99uHrzoANf9R3l9H6Cs1Ow+8IHZhi7d9BoSmgSH6ZRL9zGOS+w4he44gzrucPH5hSk7aqZyY0XMhjgEUAM0mbqc1rCLNDqSJcSUd7WouvPghTJ0+peyc/EpggdB04t89/1O/w1cDnyilFU="
)
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", DEFAULT_TOKEN).strip()
BASE_URL = os.environ.get("BASE_URL", "").strip().rstrip("/")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

REPO_OWNER = os.environ.get("GITHUB_REPO_OWNER", "nguyenngoctantgdd42852-web").strip()
REPO_NAME = os.environ.get("GITHUB_REPO_NAME", "bhx-khu-vuc-dashboard").strip()
REPO_BRANCH = os.environ.get("GITHUB_REPO_BRANCH", "main").strip()

DRIVE_INPUT_FOLDER_ID = "1yI_aUp9tLVnzw--2R6EqbYuUeCs5Vg_8"
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "static")).strip()
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
os.makedirs(STATIC_DIR, exist_ok=True)

# Ánh xạ phần tên sau "rt" -> Tên hiển thị đẹp (không ngoặc)
STORE_NAMES = {
    "ninhhiep": "NINH HIỆP",
}


def normalize_cmd(text: str) -> str:
    s = (text or "").strip().lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s)


def resolve_github_target(raw_text: str):
    """
    Trả về (cmd_clean, repo_path, local_filename, title) nếu khớp lệnh rt...
    - rtkhuvuc -> rtkhuvuc.png
    - rt<ten>  -> stores/rt<ten>.png
    """
    cmd = normalize_cmd(raw_text)
    if not cmd.startswith("rt"):
        return None

    if cmd == "rtkhuvuc":
        return cmd, "rtkhuvuc.png", "rtkhuvuc.png", "BÁO CÁO REALTIME KHU VỰC"

    if len(cmd) > 2:
        store_key = cmd[2:]
        store_label = STORE_NAMES.get(store_key, store_key.upper())
        filename = f"{cmd}.png"
        return cmd, f"stores/{filename}", filename, f"BÁO CÁO REALTIME SIÊU THỊ {store_label}"

    return None


def sync_dashboard_from_github(repo_path: str, local_filename: str):
    """Tải ảnh mới nhất từ GitHub repo về STATIC_DIR, trả về local_path nếu thành công."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}?ref={REPO_BRANCH}"
    headers = {
        "Accept": "application/vnd.github.v3.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        resp = requests.get(url, headers=headers, timeout=25)
        if resp.status_code == 200 and resp.content:
            local_path = os.path.join(STATIC_DIR, local_filename)
            with open(local_path, "wb") as f:
                f.write(resp.content)
            return local_path
        print(f"⚠️ Tải GitHub thất bại ({repo_path}): HTTP {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"❌ Lỗi kết nối GitHub ({repo_path}): {e}")
    return None


def get_latest_drive_input_time_vn() -> str:
    """Lấy thời gian sửa đổi mới nhất (HH:MM DD/MM/YYYY, UTC+7) của file trong folder input trên Drive."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not DRIVE_INPUT_FOLDER_ID:
        print("ℹ️ Chưa cấu hình GOOGLE_SERVICE_ACCOUNT_JSON hoặc DRIVE_INPUT_FOLDER_ID")
        return ""

    try:
        raw_json = GOOGLE_SERVICE_ACCOUNT_JSON.strip()
        if (raw_json.startswith("'") and raw_json.endswith("'")) or (
            raw_json.startswith('"') and raw_json.endswith('"')
        ):
            raw_json = raw_json[1:-1]

        info = json.loads(raw_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/drive.readonly"]
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        res = (
            service.files()
            .list(
                q=(
                    f"'{DRIVE_INPUT_FOLDER_ID}' in parents "
                    "and trashed = false "
                    "and mimeType != 'application/vnd.google-apps.folder'"
                ),
                orderBy="modifiedTime desc",
                pageSize=20,
                corpora="allDrives",
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                fields="files(id, name, mimeType, modifiedTime)",
            )
            .execute()
        )

        files = res.get("files", [])
        if not files:
            print("⚠️ Folder input trên Google Drive không có file nào")
            return ""

        latest_file = files[0]
        mod_iso = latest_file.get("modifiedTime", "")
        if not mod_iso:
            return ""

        dt_utc = datetime.fromisoformat(mod_iso.replace("Z", "+00:00"))
        dt_vn = dt_utc.astimezone(VN_TZ)
        formatted = dt_vn.strftime("%H:%M %d/%m/%Y")
        print(f"📁 Drive input mới nhất: {latest_file.get('name')} -> {formatted}")
        return formatted
    except Exception as e:
        print(f"⚠️ Lỗi đọc Google Drive input: {e}")
        return ""


def list_static_images():
    if not os.path.exists(STATIC_DIR):
        return []
    files = []
    for name in sorted(os.listdir(STATIC_DIR)):
        full_path = os.path.join(STATIC_DIR, name)
        if os.path.isfile(full_path) and name.lower().endswith(IMAGE_EXTS):
            files.append(name)
    return files


def _validate_line_config() -> bool:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("❌ Thiếu LINE_CHANNEL_ACCESS_TOKEN")
        return False

    if not BASE_URL or not BASE_URL.startswith("https://") or "your-tunnel" in BASE_URL:
        print("❌ Chưa cấu hình BASE_URL HTTPS thật")
        return False

    return True


def _push_line_messages(target_id: str, messages: list, label: str = ""):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }

    payload = {
        "to": target_id,
        "messages": messages,
    }

    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers=headers,
        json=payload,
        timeout=30,
    )
    suffix = f" ({label})" if label else ""
    print(f"📡 LINE Push API{suffix} status: {resp.status_code} | response: {resp.text}")
    resp.raise_for_status()


def send_text_only(target_id: str, text_content: str, label: str = "text"):
    if not _validate_line_config():
        return
    _push_line_messages(target_id, [{"type": "text", "text": text_content}], label=label)


def send_single_image(target_id: str, filename: str, title: str = ""):
    if not _validate_line_config():
        return

    input_time_str = get_latest_drive_input_time_vn()
    ver = now_vn().strftime("%Y%m%d%H%M%S")
    img_url = f"{BASE_URL}/static/{filename}?v={ver}"

    heading = title or f"ẢNH: {filename}"
    if input_time_str:
        text_content = f"{heading}\nCập nhật input lúc: {input_time_str}\n\n{img_url}"
    else:
        text_content = f"{heading}\n(Xem mốc giờ realtime trực tiếp trên ảnh)\n\n{img_url}"

    messages = [
        {"type": "text", "text": text_content},
        {
            "type": "image",
            "originalContentUrl": img_url,
            "previewImageUrl": img_url,
        },
    ]

    _push_line_messages(target_id, messages, label=filename)


@app.route("/", methods=["GET"])
def home():
    return "Bot is running", 200


@app.route("/static/<path:filename>")
def serve_static(filename):
    resp = send_from_directory(STATIC_DIR, filename)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


@app.route("/webhook", methods=["POST"])
def webhook():
    body = request.get_json(silent=True) or {}
    events = body.get("events", [])
    print(f"📩 Nhận {len(events)} event từ LINE")

    for event in events:
        if event.get("type") == "message":
            msg = event.get("message", {})
            if msg.get("type") == "text":
                raw_text = (msg.get("text") or "").strip()
                print(f"💬 Tin nhắn nhận được: '{raw_text}'")

                source = event.get("source", {})
                target_id = (
                    source.get("groupId")
                    or source.get("roomId")
                    or source.get("userId")
                )

                if not target_id:
                    continue

                rt_target = resolve_github_target(raw_text)
                if rt_target:
                    cmd_clean, repo_path, local_filename, title = rt_target
                    print(f"🎯 Đồng bộ '{repo_path}' cho lệnh '{raw_text}' -> {target_id}")
                    local_path = sync_dashboard_from_github(repo_path, local_filename)
                    if local_path and os.path.exists(local_path):
                        send_single_image(target_id, local_filename, title=title)
                    else:
                        send_text_only(
                            target_id,
                            f"⚠️ Không tìm thấy hoặc không tải được ảnh '{repo_path}' từ GitHub.",
                            label="not_found",
                        )

                elif normalize_cmd(raw_text) in ["ds", "list", "danhsachanh"]:
                    files = list_static_images()
                    if files:
                        ds_text = "📂 Danh sách ảnh đã tải về:\n" + "\n".join(f"- {f}" for f in files)
                    else:
                        ds_text = "📂 Hiện chưa có file ảnh nào trong bộ nhớ đệm."
                    send_text_only(target_id, ds_text, label="list")

                else:
                    send_text_only(
                        target_id,
                        "Gõ 'rtkhuvuc' để xem khu vực hoặc 'rt<tensiêu thị>' (vd: rtninhhiep, rt131d24).",
                        label="help",
                    )

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"🚀 Khởi động Flask tại http://0.0.0.0:{port} | BASE_URL={BASE_URL}")
    app.run(host="0.0.0.0", port=port)
