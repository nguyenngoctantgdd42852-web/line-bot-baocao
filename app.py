import json
import os
import re
import time
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

# Cấu hình chống spam / gọi trùng (giây), lưu ra file để dùng chung giữa các worker
CMD_COOLDOWN_SECONDS = int(os.environ.get("CMD_COOLDOWN_SECONDS", "180"))   # 3 phút/lệnh/nhóm
USER_COOLDOWN_SECONDS = int(os.environ.get("USER_COOLDOWN_SECONDS", "30"))  # 30 giây/người
COOLDOWN_FILE = os.path.join(STATIC_DIR, "cooldown_state.json")

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
        print(f"⚠️ Tải GitHub thất bại ({repo_path}): HTTP {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"❌ Lỗi kết nối GitHub ({repo_path}): {e}")
    return None


def fetch_github_json(repo_path: str):
    """Tải file JSON tóm tắt từ GitHub repo; trả về None nếu chưa có hoặc lỗi."""
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{repo_path}?ref={REPO_BRANCH}"
    headers = {
        "Accept": "application/vnd.github.v3.raw",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if resp.status_code == 200 and resp.content:
            return resp.json()
        print(f"ℹ️ Không tìm thấy hoặc không đọc được JSON ({repo_path}): HTTP {resp.status_code}")
    except Exception as e:
        print(f"⚠️ Lỗi tải JSON ({repo_path}): {e}")
    return None


def get_store_summary_json(cmd_clean: str):
    """Lấy JSON tóm tắt của siêu thị theo cmd_clean (ví dụ rtninhdiem hoặc ninhdiem)."""
    candidates = [f"stores/summary_{cmd_clean}.json"]
    if cmd_clean.startswith("rt") and len(cmd_clean) > 2:
        candidates.append(f"stores/summary_{cmd_clean[2:]}.json")
    else:
        candidates.append(f"stores/summary_rt{cmd_clean}.json")

    for path in candidates:
        data = fetch_github_json(path)
        if isinstance(data, dict) and data:
            return data
    return None


def build_analysis_message(title: str, data: dict) -> str:
    """Chuyển dữ liệu JSON tóm tắt thành tin nhắn phân tích ngắn gọn, dễ đọc trên LINE."""
    if not isinstance(data, dict) or not data:
        return ""

    lines = [f"📊 PHÂN TÍCH DOANH THU - {title}"]

    if data.get("dt_ngay") or data.get("base") or data.get("ht_pct"):
        lines.append(
            f"• DT ngày: {data.get('dt_ngay', '-')} | Base: {data.get('base', '-')} ({data.get('ht_pct', '-')})"
        )

    if data.get("chenh_lech"):
        lines.append(f"• Chênh lệch Base: {data['chenh_lech']}")

    if data.get("dat_base"):
        lines.append(f"• Siêu thị đạt Base: {data['dat_base']}")

    if data.get("so_bill"):
        lines.append(f"• Số bill: {data['so_bill']}")

    if data.get("kenh"):
        lines.append(f"• Kênh bán: {data['kenh']}")

    if data.get("nganh_hang"):
        lines.append(f"• Ngành hàng: {data['nganh_hang']}")

    if data.get("top_sieu_thi"):
        lines.append(f"• Top đạt cao: {data['top_sieu_thi']}")

    if data.get("can_day_sieu_thi"):
        lines.append(f"• Cần đẩy thêm: {data['can_day_sieu_thi']}")

    if data.get("top_nhom"):
        lines.append(f"• Ngành/nh
