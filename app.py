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

# Ánh xạ tên không dấu (chuẩn hóa) -> TÊN VIẾT TẮT (27 siêu thị)
STORE_NAMES = {
    "ngogiakham": "NGÔ GIA KHẢM",
    "vinhhai": "VĨNH HẢI",
    "171d24": "171 Đ2/4",
    "tubong": "TU BÔNG",
    "vanthang": "VẠN THẮNG",
    "lacan": "LẠC AN",
    "thongnhat": "THỐNG NHẤT",
    "ninhthuy": "NINH THỦY",
    "24ninhhoa": "2/4 NINH HOA",
    "chobau": "CHỢ BẦU",
    "nguyenhue": "NGUYỄN HUỆ",
    "binhthanh": "BÌNH THÀNH",
    "vinhluong": "VĨNH LƯƠNG",
    "ninhphung": "NINH PHỤNG",
    "ninhsim": "NINH SIM",
    "ninhhiep": "NINH HIỆP",
    "ninhdiem": "NINH DIÊM",
    "113d24": "113 Đ2/4",
    "vanninh": "VẠN NINH",
    "doclet": "DỐC LẾT",
    "giamy": "GIA MỸ",
    "nguyenxien": "NGUYỄN XIỂN",
    "thachthanh": "THẠCH THÀNH",
    "tranquycap": "TRẦN QUÝ CÁP",
    "maixuanthuong": "MAI XUÂN THƯỞNG",
    "duonghienquyen": "DƯƠNG HIẾN QUYỀN",
    "288d24": "288 Đ2/4",
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
        store_key = normalize_cmd(cmd[2:])
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
        print(f"⚠️ Tải GitHub thất bại ({repo_path}): HTTP {resp.status_code} - {resp.text[:200
