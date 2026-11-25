import os
from datetime import datetime
from flask import Flask, request, send_from_directory
import requests

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

# Nếu có cấu hình STATIC_DIR (ví dụ /var/data/static trên Render Disk) thì dùng, không thì dùng ./static
STATIC_DIR = os.environ.get("STATIC_DIR", os.path.join(os.path.dirname(__file__), "static")).strip()
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")
os.makedirs(STATIC_DIR, exist_ok=True)


def list_static_images():
    if not os.path.exists(STATIC_DIR):
        return []
    files = []
    for name in sorted(os.listdir(STATIC_DIR)):
        full_path = os.path.join(STATIC_DIR, name)
        if os.path.isfile(full_path) and name.lower().endswith(IMAGE_EXTS):
            files.append(name)
    return files


def find_image_in_static(query_name: str):
    cleaned = query_name.strip().lstrip("/").lower()
    if cleaned.startswith("img "):
        cleaned = cleaned[4:].strip()
    elif cleaned.startswith("/img "):
        cleaned = cleaned[5:].strip()

    if not cleaned:
        return None

    files = list_static_images()

    for fname in files:
        if fname.lower() == cleaned:
            return fname

    for fname in files:
        stem, _ = os.path.splitext(fname.lower())
        if stem == cleaned:
            return fname

    return None


def resolve_report_image(base_name: str):
    candidates = [base_name]
    if base_name == "homnay":
        candidates.append("ngay")
    elif base_name == "ngay":
        candidates.append("homnay")

    for name in candidates:
        for ext in ("jpg", "jpeg", "png"):
            filename = f"{name}.{ext}"
            full_path = os.path.join(STATIC_DIR, filename)
            if os.path.exists(full_path):
                return filename, full_path
    return None, None


def generate_report_images(mode: str = "all"):
    thang_file, thang_path = resolve_report_image("thang")
    homnay_file, homnay_path = resolve_report_image("homnay")

    if mode == "thang":
        if not thang_path:
            print(f"⚠️ Thiếu file thang.jpg hoặc thang.png trong {STATIC_DIR}")
    elif mode == "homnay":
        if not homnay_path:
            print(f"⚠️ Thiếu file homnay.jpg hoặc homnay.png trong {STATIC_DIR}")
    else:
        if not thang_path:
            print(f"⚠️ Thiếu file thang.jpg hoặc thang.png trong {STATIC_DIR}")
        if not homnay_path:
            print(f"⚠️ Thiếu file homnay.jpg hoặc homnay.png trong {STATIC_DIR}")

    return thang_path, homnay_path


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

    now_str = datetime.now().strftime("%H:%M %d/%m/%Y")
    ver = datetime.now().strftime("%Y%m%d%H%M%S")
    img_url = f"{BASE_URL}/static/{filename}?v={ver}"

    heading = title or f"ẢNH: {filename}"
    text_content = f"{heading}\nCập nhật lúc: {now_str}\n\n{img_url}"

    messages = [
        {"type": "text", "text": text_content},
        {
            "type": "image",
            "originalContentUrl": img_url,
            "previewImageUrl": img_url,
        },
    ]

    _push_line_messages(target_id, messages, label=filename)


def send_line_report(target_id: str):
    if not _validate_line_config():
        return

    thang_file, _ = resolve_report_image("thang")
    homnay_file, _ = resolve_report_image("homnay")

    if not thang_file or not homnay_file:
        print(f"❌ Không tìm thấy đủ ảnh tháng hoặc hôm nay (.jpg / .png) trong {STATIC_DIR}")
        return

    now_str = datetime.now().strftime("%H:%M %d/%m/%Y")
    ver = datetime.now().strftime("%Y%m%d%H%M%S")

    url_thang = f"{BASE_URL}/static/{thang_file}?v={ver}"
    url_homnay = f"{BASE_URL}/static/{homnay_file}?v={ver}"

    text_content = (
        f"BÁO CÁO DOANH THU KHU VỰC\n"
        f"Cập nhật lúc: {now_str}\n\n"
        f"1. Lũy kế & dự kiến tháng:\n{url_thang}\n\n"
        f"2. Doanh thu hôm nay:\n{url_homnay}"
    )

    messages = [
        {"type": "text", "text": text_content},
        {
            "type": "image",
            "originalContentUrl": url_thang,
            "previewImageUrl": url_thang,
        },
        {
            "type": "image",
            "originalContentUrl": url_homnay,
            "previewImageUrl": url_homnay,
        },
    ]

    _push_line_messages(target_id, messages, label="all")


def send_thang_report(target_id: str):
    thang_file, _ = resolve_report_image("thang")
    if not thang_file:
        print(f"❌ Không tìm thấy ảnh tháng (.jpg / .png) trong {STATIC_DIR}")
        return
    send_single_image(target_id, thang_file, title="BÁO CÁO LŨY KẾ & DỰ KIẾN THÁNG")


def send_homnay_report(target_id: str):
    homnay_file, _ = resolve_report_image("homnay")
    if not homnay_file:
        print(f"❌ Không tìm thấy ảnh hôm nay (.jpg / .png) trong {STATIC_DIR}")
        return
    send_single_image(target_id, homnay_file, title="BÁO CÁO DOANH THU HÔM NAY")


@app.route("/", methods=["GET"])
def home():
    return "Bot is running", 200


@app.route("/static/<path:filename>")
def serve_static(filename):
    return send_from_directory(STATIC_DIR, filename)


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
                cmd = raw_text.lower()
                print(f"💬 Tin nhắn nhận được: '{raw_text}'")

                source = event.get("source", {})
                target_id = (
                    source.get("groupId")
                    or source.get("roomId")
                    or source.get("userId")
                )

                if not target_id:
                    continue

                if cmd in ["/bc", "baocao", "bao cao", "báo cáo"]:
                    print(f"🎯 Gửi báo cáo đầy đủ tới target_id: {target_id}")
                    generate_report_images("all")
                    send_line_report(target_id)

                elif cmd in ["/thang", "thang", "bao cao thang", "báo cáo tháng"]:
                    print(f"🎯 Gửi báo cáo THÁNG tới target_id: {target_id}")
                    generate_report_images("thang")
                    send_thang_report(target_id)

                elif cmd in ["/hn", "/ngay", "ngay", "bao cao ngay", "báo cáo ngày", "/homnay", "hom nay", "homnay", "bao cao hom nay", "báo cáo hôm nay"]:
                    print(f"🎯 Gửi báo cáo HÔM NAY tới target_id: {target_id}")
                    generate_report_images("homnay")
                    send_homnay_report(target_id)

                elif cmd in ["/ds", "/list", "ds", "danh sach anh", "danh sách ảnh"]:
                    files = list_static_images()
                    if files:
                        ds_text = "📂 Danh sách ảnh hiện có:\n" + "\n".join(f"- {f}" for f in files)
                    else:
                        ds_text = "📂 Hiện chưa có file ảnh nào."
                    send_text_only(target_id, ds_text, label="list")

                else:
                    matched_file = find_image_in_static(cmd)
                    if matched_file:
                        print(f"🖼️ Tìm thấy ảnh '{matched_file}' khớp lệnh '{raw_text}'")
                        send_single_image(target_id, matched_file)

    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    print(f"🚀 Khởi động Flask tại http://0.0.0.0:{port} | BASE_URL={BASE_URL}")
    app.run(host="0.0.0.0", port=port)