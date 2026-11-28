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

# Cấu hình dashboard tháng (ảnh khu vực, ảnh từng siêu thị trong stores/, summary_thang.json)
MONTH_REPO_OWNER = os.environ.get("MONTH_GITHUB_OWNER", REPO_OWNER).strip()
MONTH_REPO_NAME = os.environ.get("MONTH_GITHUB_REPO", "bhx-thang-dashboard").strip()
MONTH_REPO_BRANCH = os.environ.get("MONTH_GITHUB_BRANCH", "main").strip()
MONTH_IMAGE_REPO_PATH = os.environ.get("MONTH_IMAGE_PATH", "rtthangkhuvuc.png").strip().lstrip("/")
MONTH_STORE_FOLDER = os.environ.get("MONTH_STORE_FOLDER", "stores").strip().strip("/") or "stores"
MONTH_SUMMARY_REPO_PATH = os.environ.get("MONTH_SUMMARY_PATH", "summary_thang.json").strip().lstrip("/")

DRIVE_INPUT_FOLDER_ID = "1yI_aUp9tLVnzw--2R6EqbYuUeCs5Vg_8"
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()

STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "static")).strip()
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
os.makedirs(STATIC_DIR, exist_ok=True)

# Chặn đúng lệnh trùng trong 3 phút (180 giây), sau 3 phút tự gỡ chặn
CMD_COOLDOWN_SECONDS = int(os.environ.get("CMD_COOLDOWN_SECONDS", "180"))
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

STORE_ALIASES = {
    "171duong24": "171d24",
    "113duong24": "113d24",
    "288duong24": "288d24",
    "24ninhhoakhanhhoa": "24ninhhoa",
}


def normalize_cmd(text: str) -> str:
    s = (text or "").strip().lower().replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]", "", s)


def resolve_store_key(raw_token: str) -> str:
    k = normalize_cmd(raw_token)
    if k in STORE_ALIASES:
        return STORE_ALIASES[k]
    if k in STORE_NAMES:
        return k
    for alias, canon in STORE_ALIASES.items():
        if k == alias:
            return canon
    return k


def resolve_github_target(raw_text: str):
    cmd = normalize_cmd(raw_text)
    if not cmd:
        return None

    # 1. Lệnh báo cáo doanh thu tháng khu vực
    if cmd in {"rtthangkhuvuc", "rtthangkhuvucthang", "rtthangkv", "rtthang"}:
        return (
            "rtthangkhuvuc",
            MONTH_IMAGE_REPO_PATH,
            "rtthangkhuvuc.png",
            "BÁO CÁO DOANH THU THÁNG KHU VỰC",
            MONTH_REPO_OWNER,
            MONTH_REPO_NAME,
            MONTH_REPO_BRANCH,
        )

    # 2. Lệnh báo cáo doanh thu tháng theo từng siêu thị (vd: rtthangninhdiem, rthang171d24...)
    for prefix in ("rtthang", "rthang"):
        if cmd.startswith(prefix) and len(cmd) > len(prefix):
            store_key = resolve_store_key(cmd[len(prefix):])
            store_label = STORE_NAMES.get(store_key, store_key.upper())
            filename = f"rtthang{store_key}.png"
            return (
                f"rtthang{store_key}",
                f"{MONTH_STORE_FOLDER}/{filename}",
                filename,
                f"BÁO CÁO DOANH THU THÁNG SIÊU THỊ {store_label}",
                MONTH_REPO_OWNER,
                MONTH_REPO_NAME,
                MONTH_REPO_BRANCH,
            )

    if not cmd.startswith("rt"):
        return None

    # 3. Lệnh báo cáo realtime ngày khu vực
    if cmd in {"rtkhuvuc", "rtkv", "rtngaykhuvuc", "rtngaykv"}:
        return (
            "rtkhuvuc",
            "rtkhuvuc.png",
            "rtkhuvuc.png",
            "BÁO CÁO REALTIME KHU VỰC",
            REPO_OWNER,
            REPO_NAME,
            REPO_BRANCH,
        )

    # 4. Lệnh báo cáo realtime ngày theo từng siêu thị
    if len(cmd) > 2:
        raw_store = cmd[4:] if cmd.startswith("rtngay") and len(cmd) > 6 else cmd[2:]
        store_key = resolve_store_key(raw_store)
        store_label = STORE_NAMES.get(store_key, store_key.upper())
        day_cmd = f"rt{store_key}"
        filename = f"{day_cmd}.png"
        return (
            day_cmd,
            f"stores/{filename}",
            filename,
            f"BÁO CÁO REALTIME SIÊU THỊ {store_label}",
            REPO_OWNER,
            REPO_NAME,
            REPO_BRANCH,
        )

    return None


def sync_dashboard_from_github(
    repo_path: str,
    local_filename: str,
    owner: str = REPO_OWNER,
    repo: str = REPO_NAME,
    branch: str = REPO_BRANCH,
):
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{repo_path}?ref={branch}"
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
        print(f"⚠️ Tải GitHub thất bại ({owner}/{repo}/{repo_path}): HTTP {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"❌ Lỗi kết nối GitHub ({owner}/{repo}/{repo_path}): {e}")
    return None


def fetch_github_json(
    repo_path: str,
    owner: str = REPO_OWNER,
    repo: str = REPO_NAME,
    branch: str = REPO_BRANCH,
):
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{repo_path}?ref={branch}"
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
        print(f"ℹ️ Không tìm thấy hoặc không đọc được JSON ({owner}/{repo}/{repo_path}): HTTP {resp.status_code}")
    except Exception as e:
        print(f"⚠️ Lỗi tải JSON ({owner}/{repo}/{repo_path}): {e}")
    return None


def get_store_summary_json(cmd_clean: str):
    if cmd_clean.startswith("rtthang"):
        store_key = cmd_clean[len("rtthang"):]
        candidates = [
            f"{MONTH_STORE_FOLDER}/summary_{cmd_clean}.json",
            f"{MONTH_STORE_FOLDER}/summary_{store_key}.json",
            f"summary_{cmd_clean}.json",
            f"summary_{store_key}.json",
        ]
        for path in candidates:
            data = fetch_github_json(
                path,
                owner=MONTH_REPO_OWNER,
                repo=MONTH_REPO_NAME,
                branch=MONTH_REPO_BRANCH,
            )
            if isinstance(data, dict) and data:
                return data
        return None

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
    if not isinstance(data, dict) or not data:
        return ""

    lines = [f"📊 PHÂN TÍCH DOANH THU - {title}"]

    # --- ĐỊNH DẠNG CHO BÁO CÁO THÁNG ---
    if data.get("dt_thang_du_kien"):
        thang_ht = data.get("thang_hien_tai", "")
        lines.append(
            f"• Lũy kế đến: {data.get('den_ngay', '-')} ({data.get('ngay_luy_ke', '-')}/{data.get('tong_ngay_thang', '-')} ngày)"
        )
        lines.append(f"• Dự kiến T{thang_ht}: {data['dt_thang_du_kien']}")
        if data.get("tang_truong_thang"):
            lines.append(f"• So với tháng trước: {data['tang_truong_thang']}")
        if data.get("chi_so_bq"):
            lines.append(f"• Chỉ số BQ: {data['chi_so_bq']}")
        if data.get("nhom_lon"):
            lines.append(f"• Nhóm hàng: {data['nhom_lon']}")
        if data.get("top_nganh_tang"):
            lines.append(f"• Ngành tăng tốt: {data['top_nganh_tang']}")
        if data.get("top_nganh_giam"):
            lines.append(f"• Ngành giảm sâu: {data['top_nganh_giam']}")
        if data.get("sieu_thi_tang"):
            lines.append(f"• Siêu thị tăng: {data['sieu_thi_tang']}")
        if data.get("sieu_thi_can_day"):
            lines.append(f"• Siêu thị cần đẩy: {data['sieu_thi_can_day']}")
        if data.get("nhan_dinh"):
            lines.append(f"💡 Nhận định: {data['nhan_dinh']}")
        return "\n".join(lines)

    # --- ĐỊNH DẠNG CHO BÁO CÁO NGÀY ---
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
        lines.append(f"• Ngành/nhóm nổi bật: {data['top_nhom']}")

    if data.get("nhan_dinh"):
        lines.append(f"💡 Nhận định: {data['nhan_dinh']}")

    return "\n".join(lines) if len(lines) > 1 else ""


def get_latest_drive_input_time_vn() -> str:
    if not GOOGLE_SERVICE_ACCOUNT_JSON or not DRIVE_INPUT_FOLDER_ID:
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
            return ""

        latest_file = files[0]
        mod_iso = latest_file.get("modifiedTime", "")
        if not mod_iso:
            return ""

        dt_utc = datetime.fromisoformat(mod_iso.replace("Z", "+00:00"))
        dt_vn = dt_utc.astimezone(VN_TZ)
        return dt_vn.strftime("%H:%M %d/%m/%Y")
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


def _validate_line_config(require_base_url: bool = True) -> bool:
    if not LINE_CHANNEL_ACCESS_TOKEN:
        print("❌ Thiếu LINE_CHANNEL_ACCESS_TOKEN")
        return False

    if require_base_url:
        if not BASE_URL or not BASE_URL.startswith("https://") or "your-tunnel" in BASE_URL:
            print("❌ Chưa cấu hình BASE_URL HTTPS thật")
            return False

    return True


def _send_line_messages(target_id: str, messages: list, reply_token: str = "", label: str = ""):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
    }
    suffix = f" ({label})" if label else ""

    if reply_token:
        resp = requests.post(
            "https://api.line.me/v2/bot/message/reply",
            headers=headers,
            json={"replyToken": reply_token, "messages": messages},
            timeout=30,
        )
        print(f"📡 LINE Reply API{suffix} status: {resp.status_code}")
        if resp.status_code == 200:
            return
        print(f"⚠️ Reply API lỗi ({resp.text}), chuyển sang Push API...")

    resp = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers=headers,
        json={"to": target_id, "messages": messages},
        timeout=30,
    )
    print(f"📡 LINE Push API{suffix} status: {resp.status_code} | response: {resp.text}")
    resp.raise_for_status()


def send_text_only(target_id: str, text_content: str, reply_token: str = "", label: str = "text"):
    if not _validate_line_config(require_base_url=False):
        return
    _send_line_messages(
        target_id,
        [{"type": "text", "text": text_content[:4900]}],
        reply_token=reply_token,
        label=label,
    )


def send_single_image(
    target_id: str,
    filename: str,
    title: str = "",
    analysis_text: str = "",
    reply_token: str = "",
):
    if not _validate_line_config(require_base_url=True):
        return

    input_time_str = get_latest_drive_input_time_vn()
    ver = now_vn().strftime("%Y%m%d%H%M%S")
    img_url = f"{BASE_URL}/static/{filename}?v={ver}"

    heading = title or f"ẢNH: {filename}"
    if input_time_str:
        text_content = f"{heading}\nCập nhật input lúc: {input_time_str}\n\n{img_url}"
    else:
        text_content = f"{heading}\n(Xem mốc giờ realtime trực tiếp trên ảnh)\n\n{img_url}"

    if analysis_text:
        text_content = f"{text_content}\n\n{analysis_text}"

    messages = [
        {"type": "text", "text": text_content[:4900]},
        {
            "type": "image",
            "originalContentUrl": img_url,
            "previewImageUrl": img_url,
        },
    ]

    _send_line_messages(target_id, messages, reply_token=reply_token, label=filename)


def _load_cmd_cooldowns() -> dict:
    if not os.path.exists(COOLDOWN_FILE):
        return {}
    try:
        with open(COOLDOWN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                if isinstance(data.get("cmd"), dict):
                    return data["cmd"]
                return {k: v for k, v in data.items() if isinstance(v, (int, float))}
    except Exception as e:
        print(f"⚠️ Lỗi đọc cooldown_state.json: {e}")
    return {}


def _save_cmd_cooldowns(cmd_state: dict):
    try:
        with open(COOLDOWN_FILE, "w", encoding="utf-8") as f:
            json.dump({"cmd": cmd_state}, f)
    except Exception as e:
        print(f"⚠️ Lỗi ghi cooldown_state.json: {e}")


def check_and_mark_cooldown(target_id: str, cmd_clean: str) -> bool:
    """
1. Trong CMD_COOLDOWN_SECONDS (180s = 3 phút): gọi lại đúng lệnh trùng tại cùng target_id -> trả về False (không phản hồi).
2. Sau 180s: gỡ bỏ trạng thái chặn của lệnh đó và đánh dấu lượt gọi mới -> trả về True (phản hồi lại).
    """
    now_ts = time.time()
    cmd_state = _load_cmd_cooldowns()

    active_state = {
        k: float(ts)
        for k, ts in cmd_state.items()
        if (now_ts - float(ts or 0)) < CMD_COOLDOWN_SECONDS
    }

    cmd_key = f"{target_id}::{cmd_clean}"
    if cmd_key in active_state:
        remain = int(CMD_COOLDOWN_SECONDS - (now_ts - active_state[cmd_key]))
        print(f"⏳ Đang chặn lệnh trùng '{cmd_clean}' tại {target_id} (còn {max(1, remain)}s)")
        _save_cmd_cooldowns(active_state)
        return False

    active_state[cmd_key] = now_ts
    _save_cmd_cooldowns(active_state)
    return True


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
        if event.get("type") != "message":
            continue

        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue

        raw_text = (msg.get("text") or "").strip()
        reply_token = event.get("replyToken", "")
        source = event.get("source", {})
        user_id = source.get("userId", "")
        target_id = (
            source.get("groupId")
            or source.get("roomId")
            or user_id
        )

        if not target_id:
            continue

        rt_target = resolve_github_target(raw_text)
        if rt_target:
            cmd_clean, repo_path, local_filename, title, src_owner, src_repo, src_branch = rt_target

            if not check_and_mark_cooldown(target_id, cmd_clean):
                continue

            print(f"🎯 Đồng bộ '{src_owner}/{src_repo}/{repo_path}' cho lệnh '{raw_text}' -> {target_id}")
            local_path = sync_dashboard_from_github(
                repo_path,
                local_filename,
                owner=src_owner,
                repo=src_repo,
                branch=src_branch,
            )
            if local_path and os.path.exists(local_path):
                if cmd_clean == "rtthangkhuvuc":
                    summary_data = fetch_github_json(
                        MONTH_SUMMARY_REPO_PATH,
                        owner=src_owner,
                        repo=src_repo,
                        branch=src_branch,
                    )
                elif cmd_clean == "rtkhuvuc":
                    summary_data = fetch_github_json("summary.json")
                else:
                    summary_data = get_store_summary_json(cmd_clean)

                analysis_text = build_analysis_message(title, summary_data)
                send_single_image(
                    target_id,
                    local_filename,
                    title=title,
                    analysis_text=analysis_text,
                    reply_token=reply_token,
                )
            else:
                send_text_only(
                    target_id,
                    f"⚠️ Không tìm thấy báo cáo '{raw_text}'. Vui lòng kiểm tra lại tên siêu thị hoặc báo cáo.",
                    reply_token=reply_token,
                    label="not_found",
                )

        elif normalize_cmd(raw_text) in ["ds", "list", "danhsachanh"]:
            if not check_and_mark_cooldown(target_id, "ds"):
                continue
            files = list_static_images()
            if files:
                ds_text = "📂 Danh sách ảnh đã tải về:\n" + "\n".join(f"- {f}" for f in files)
            else:
                ds_text = "📂 Hiện chưa có file ảnh nào trong bộ nhớ đệm."
            send_text_only(target_id, ds_text, reply_token=reply_token, label="list")

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"🚀 Khởi động Flask tại http://0.0.0.0:{port} | BASE_URL={BASE_URL}")
    app.run(host="0.0.0.0", port=port)
