"""
core.py - Toàn bộ logic nghiệp vụ dùng chung giữa Bot Telegram và Web App.
Không phụ thuộc vào Telegram hay Flask - chỉ là các hàm Python thuần,
để cả bot_runtime.py (Telegram) và app.py (Flask) đều import và dùng lại.
"""

import os
import re
import html
import random
import time
from datetime import datetime

import requests
from requests.auth import HTTPBasicAuth

from google import genai

import gspread
import gspread.exceptions
from google.oauth2.service_account import Credentials

TEXT_MODEL = "gemini-2.5-flash"
FB_GRAPH_VERSION = "v21.0"

DEFAULT_CTA_TEXT = os.getenv(
    "DEFAULT_CTA_TEXT",
    "Liên hệ ngay với chúng tôi để được tư vấn miễn phí!",
)

# Gemini API Key CHUNG của hệ thống - dùng cho mọi tài khoản đang trong thời gian
# dùng thử hoặc đã mua gói. Khách không cần tự mang API key riêng nữa.
SYSTEM_GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ---------- Bảng giá (VNĐ) ----------
# Ưu đãi lần đầu áp dụng cho ĐƠN HÀNG ĐẦU TIÊN của mỗi tài khoản, bất kể chọn gói nào.
# "slots" = số bài được phép soạn ĐỒNG THỜI (mở nhiều tab viết bài cùng lúc trong 1 lượt truy cập).
PLANS = {
    "1m":       {"label": "1 tháng",   "months": 1,    "price": 149000,   "first_price": 99000,  "slots": 1, "max_sites": 1},
    "3m":       {"label": "3 tháng",   "months": 3,    "price": 399000,   "first_price": 299000, "slots": 2, "max_sites": 2},
    "6m":       {"label": "6 tháng",   "months": 6,    "price": 699000,   "first_price": 549000, "slots": 3, "max_sites": 3},
    "12m":      {"label": "12 tháng",  "months": 12,   "price": 1199000,  "first_price": 899000, "slots": 4, "max_sites": 4},
    # "Vĩnh viễn" = mẹo kỹ thuật months=1200 (100 năm), không cần đổi cấu trúc database.
    # max_sites: None = KHÔNG GIỚI HẠN
    "lifetime": {"label": "Vĩnh viễn", "months": 1200, "price": 3999999,  "first_price": 3999999, "slots": 5, "max_sites": None},
}

# Số lượt viết bài đồng thời / số website mặc định cho tài khoản đang dùng thử (chưa mua gói nào)
TRIAL_WRITE_SLOTS = 1
TRIAL_MAX_SITES = 1

VIETQR_BANK_ID = os.getenv("VIETQR_BANK_ID")       # VD: 970436 (Vietcombank) - xem danh sách tại vietqr.io
VIETQR_ACCOUNT_NO = os.getenv("VIETQR_ACCOUNT_NO")
VIETQR_ACCOUNT_NAME = os.getenv("VIETQR_ACCOUNT_NAME")


def format_vnd(amount: int) -> str:
    return f"{amount:,.0f}".replace(",", ".") + "đ"


def plan_price_for(plan_code: str, is_first_order: bool) -> int:
    plan = PLANS[plan_code]
    return plan["first_price"] if is_first_order else plan["price"]


def plan_monthly_equivalent(plan_code: str, price: int = None):
    """Giá quy đổi theo tháng - None cho gói Vĩnh viễn. Truyền price= để tính theo giá ưu đãi lần đầu."""
    plan = PLANS[plan_code]
    if plan["months"] >= 1200:
        return None
    p = price if price is not None else plan["price"]
    return round(p / plan["months"])


def plan_savings_pct(plan_code: str) -> int:
    """% tiết kiệm so với mua lẻ gói 1 tháng nhiều lần - tính động từ giá thật, luôn chính xác
    (không hardcode số cố định, tự đúng nếu sau này đổi giá)."""
    plan = PLANS[plan_code]
    if plan["months"] >= 1200 or plan_code == "1m":
        return 0
    base_monthly = PLANS["1m"]["price"]
    this_monthly = plan["price"] / plan["months"]
    return max(0, round((1 - this_monthly / base_monthly) * 100))


def build_vietqr_url(amount: int, order_code: str) -> str:
    """Trả về URL ảnh QR VietQR để khách quét chuyển khoản. Trả về None nếu chưa cấu hình ngân hàng."""
    if not (VIETQR_BANK_ID and VIETQR_ACCOUNT_NO):
        return None
    import urllib.parse
    info = urllib.parse.quote(f"{order_code}")
    name = urllib.parse.quote(VIETQR_ACCOUNT_NAME or "")
    return (
        f"https://img.vietqr.io/image/{VIETQR_BANK_ID}-{VIETQR_ACCOUNT_NO}-compact2.png"
        f"?amount={amount}&addInfo={info}&accountName={name}"
    )

# ---------- Google Sheets: client dùng chung (Service Account của chủ hệ thống) ----------

SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def build_gspread_client():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        return None, None
    import json
    info = json.loads(raw)
    creds = Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)
    client = gspread.authorize(creds)
    return client, info.get("client_email")


GSPREAD_CLIENT, SERVICE_ACCOUNT_EMAIL = build_gspread_client()


# ---------- Viết bài (Gemini) ----------

def generate_article(topic: str, gemini_api_key: str = None) -> dict:
    """Gọi Gemini để viết bài. Mặc định dùng SYSTEM_GEMINI_API_KEY (key chung của hệ thống,
    áp dụng cho tài khoản đang dùng thử hoặc đã mua gói). Vẫn cho phép truyền key riêng nếu cần."""
    api_key = gemini_api_key or SYSTEM_GEMINI_API_KEY
    if not api_key:
        raise RuntimeError("Hệ thống chưa cấu hình GEMINI_API_KEY.")
    client = genai.Client(api_key=api_key)
    prompt = f"""Bạn là một biên tập viên chuyên viết bài blog tiếng Việt, chuẩn SEO.
Chủ đề: "{topic}"

Hãy tạo:
1. Một Head key (từ khóa chính, 3-6 từ, tiếng Việt, không dấu ngoặc)
2. Một tiêu đề bài viết hấp dẫn, chuẩn SEO
3. Nội dung bài viết đầy đủ (600-900 từ) theo ĐÚNG cấu trúc sau:
   - Mở đầu bằng 1-2 đoạn <p> giới thiệu chủ đề, KHÔNG có tiêu đề riêng cho phần mở đầu
   - Tiếp theo là các phần lớn, mỗi phần có tiêu đề đánh số La Mã (I., II., III., IV...) bọc trong thẻ <h2>,
     bên dưới là các đoạn <p> nội dung
   - Nếu một phần lớn cần chia mục nhỏ hơn, dùng tiêu đề đánh số thường (1., 2., 3...) bọc trong thẻ <h3>
   - Nếu cần liệt kê nhiều ý trong 1 phần, viết mỗi ý thành 1 đoạn <p> riêng biệt, nối tiếp nhau,
     TUYỆT ĐỐI KHÔNG dùng gạch đầu dòng, KHÔNG dùng số thứ tự kiểu danh sách, KHÔNG dùng thẻ <ul>/<li>/<ol>
   - TUYỆT ĐỐI KHÔNG dùng các thẻ <strong>, <b>, <em>, <i> ở bất kỳ đâu trong bài
   - Phần cuối cùng là tiêu đề "Kết luận" bọc trong <h2>Kết luận</h2>, theo sau là 1 đoạn <p> tóm tắt ngắn gọn
   - KHÔNG tự thêm câu kêu gọi hành động (CTA) hay lời mời liên hệ ở cuối bài, phần này sẽ được thêm riêng

Trả lời CHÍNH XÁC theo format sau, không thêm lời dẫn hay giải thích nào khác:
HEAD KEY: <head key>
TIÊU ĐỀ: <tiêu đề>
---
<toàn bộ nội dung bài viết dạng HTML>
"""
    response = client.models.generate_content(model=TEXT_MODEL, contents=prompt)
    text = response.text.strip()

    headkey, title = topic, topic
    content = text

    if "---" in text:
        header, body = text.split("---", 1)
        content = body.strip()
        for line in header.splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            label, _, value = line.partition(":")
            label = label.strip().upper()
            value = value.strip()
            if "HEAD KEY" in label:
                headkey = value
            elif "TIÊU ĐỀ" in label or "TIEU DE" in label:
                title = value

    return {"headkey": headkey, "title": title, "content": content}


def test_gemini_key(gemini_api_key: str):
    """Ném lỗi nếu key không hợp lệ - dùng để kiểm tra nhanh khi người dùng kết nối key mới."""
    client = genai.Client(api_key=gemini_api_key)
    client.models.generate_content(model=TEXT_MODEL, contents="Xin chào")


def append_cta(content: str, cta_text: str, cta_url: str) -> str:
    cta_html = f'<p><a href="{cta_url}">{html.escape(cta_text)}</a></p>'
    return content.rstrip() + "\n" + cta_html


def strip_html_tags(html_content: str) -> str:
    text = html_content
    text = re.sub(r"<h[1-6][^>]*>(.*?)</h[1-6]>", r"\n\1\n", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p[^>]*>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def html_to_preview_text(html_content: str) -> str:
    """Plain text để hiển thị xem trước (dùng chung cho cả web và Telegram)."""
    return strip_html_tags(html_content)


def build_share_text(article: dict, link: str) -> str:
    plain = strip_html_tags(article["content"])
    paragraphs = [p.strip() for p in plain.split("\n\n") if p.strip()]
    excerpt = paragraphs[0] if paragraphs else ""
    if len(excerpt) > 300:
        excerpt = excerpt[:300].rsplit(" ", 1)[0] + "..."
    hashtag = "#" + re.sub(r"\s+", "", article["headkey"])
    return (
        f"📌 {article['title']}\n\n"
        f"{excerpt}\n\n"
        f"👉 Đọc toàn bộ bài viết tại: {link}\n\n"
        f"{hashtag}"
    )


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text)
    return text[:50] or "anh"


# ---------- Pollinations.ai (tạo ảnh AI miễn phí 100%, không cần API key, theo chủ đề bài viết) ----------

def translate_to_image_query(topic_or_headkey: str) -> str:
    """Dùng Gemini rút gọn chủ đề tiếng Việt thành 1 câu mô tả hình ảnh cụ thể bằng tiếng Anh -
    mô hình tạo ảnh AI hiểu và ra ảnh đúng chủ đề hơn nhiều so với đưa thẳng câu hỏi tiếng Việt."""
    api_key = SYSTEM_GEMINI_API_KEY
    if not api_key:
        return topic_or_headkey  # không có key thì đành thử tạo ảnh bằng câu gốc, còn hơn không

    client = genai.Client(api_key=api_key)
    prompt = f"""Chủ đề bài viết (tiếng Việt): "{topic_or_headkey}"

Viết 1 câu mô tả hình ảnh (image prompt) bằng tiếng Anh để minh hoạ ĐÚNG chủ đề trên - mô tả CỤ THỂ
đối tượng, bối cảnh, hoạt động THẬT liên quan trực tiếp đến nội dung chủ đề (không phải khái niệm
trừu tượng chung chung).

QUAN TRỌNG: bám sát NGỮ CẢNH CỤ THỂ của chủ đề, ví dụ:
- Chủ đề nhắc tới ngành công nghiệp/nhà máy -> mô tả cảnh dây chuyền sản xuất, robot công nghiệp,
  cánh tay robot trong nhà máy, công nhân vận hành máy móc.
- Chủ đề nhắc tới y tế/sức khỏe -> mô tả cảnh bệnh viện, bác sĩ, thiết bị y tế.
- Chủ đề nhắc tới giáo dục -> mô tả cảnh lớp học, học sinh, sách vở, màn hình học tập.
- Chủ đề chung chung về công nghệ/AI (không có ngành cụ thể) -> mới mô tả cảnh công nghệ, máy tính,
  màn hình dữ liệu.
TUYỆT ĐỐI KHÔNG mặc định dùng hình ảnh "bộ não số phát sáng" hay "hành lang công nghệ tương lai"
cho mọi chủ đề - chỉ dùng khi chủ đề THỰC SỰ không có ngành/bối cảnh cụ thể nào khác.

Chỉ trả lời đúng 1 câu mô tả tiếng Anh, KHÔNG giải thích, KHÔNG dấu ngoặc kép."""

    try:
        response = client.models.generate_content(model=TEXT_MODEL, contents=prompt)
        result = response.text.strip().strip('"').strip("'").split("\n")[0].strip()
        return result or topic_or_headkey
    except Exception:
        return topic_or_headkey


def generate_pollinations_image(prompt: str, width: int = 1024, height: int = 683, seed: int = None, max_retries: int = 3) -> bytes:
    """Tạo 1 ảnh AI theo mô tả (prompt) bằng Pollinations.ai - miễn phí 100%, không cần đăng ký,
    không cần API key. seed khác nhau -> ra ảnh khác nhau dù cùng 1 prompt (dùng cho nút 'Đổi ảnh khác').
    Tự thử lại nếu gặp lỗi 429 (quá tải/quá nhiều yêu cầu) - dịch vụ miễn phí không token nên
    thỉnh thoảng bị giới hạn tốc độ, thử lại sau vài giây thường sẽ qua."""
    if seed is None:
        seed = random.randint(1, 999999)
    encoded_prompt = requests.utils.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}"
    params = {"width": width, "height": height, "seed": seed, "nologo": "true"}

    last_error = None
    for attempt in range(max_retries):
        try:
            response = requests.get(url, params=params, timeout=60)
            if response.status_code == 429:
                last_error = requests.exceptions.HTTPError("429 Too Many Requests")
                if attempt < max_retries - 1:
                    time.sleep(3 + attempt * 2)  # chờ tăng dần: 3s rồi 5s
                continue
            response.raise_for_status()
            return response.content
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(3 + attempt * 2)

    raise RuntimeError(
        f"Pollinations.ai đang quá tải (đã thử lại {max_retries} lần). "
        f"Vui lòng bấm 'Đổi ảnh khác' để thử lại, hoặc tự tải ảnh lên."
    ) from last_error


def auto_images_for_topic(query: str):
    """Trả về (cover_bytes, inline_bytes, prompt) - 2 ảnh AI khác nhau khớp chủ đề bài viết,
    cùng prompt đã dùng (để nút 'Đổi ảnh khác' tái sử dụng, không cần dịch lại qua Gemini mỗi lần).
    Gọi TUẦN TỰ (không song song) - dịch vụ miễn phí không token dễ bị chặn 429 nếu gọi dồn dập
    cùng lúc, tuần tự an toàn hơn dù chậm hơn đôi chút."""
    prompt = translate_to_image_query(query)
    cover_bytes = generate_pollinations_image(prompt, width=1200, height=630)
    inline_bytes = generate_pollinations_image(prompt, width=1000, height=750)
    return cover_bytes, inline_bytes, prompt


# ---------- WordPress ----------

def get_wp_categories(wp_url: str, wp_username: str, wp_app_password: str) -> list:
    endpoint = f"{wp_url}/wp-json/wp/v2/categories"
    response = requests.get(
        endpoint, params={"per_page": 50},
        auth=HTTPBasicAuth(wp_username, wp_app_password), timeout=20,
    )
    response.raise_for_status()
    return [{"id": c["id"], "name": c["name"]} for c in response.json()]


def upload_image_to_wordpress(wp_url: str, wp_username: str, wp_app_password: str,
                               image_bytes: bytes, filename: str, content_type: str = "image/jpeg") -> dict:
    endpoint = f"{wp_url}/wp-json/wp/v2/media"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Type": content_type,
    }
    response = requests.post(
        endpoint, auth=HTTPBasicAuth(wp_username, wp_app_password),
        headers=headers, data=image_bytes, timeout=60,
    )
    response.raise_for_status()
    return response.json()


def insert_image_into_content(content: str, image_url: str, alt_text: str) -> str:
    img_tag = f'<img src="{image_url}" alt="{alt_text}" style="max-width:100%;height:auto;" />'
    idx = content.find("</p>")
    if idx != -1:
        insert_pos = idx + len("</p>")
        return content[:insert_pos] + f"\n{img_tag}\n" + content[insert_pos:]
    return img_tag + "\n" + content


def post_to_wordpress(wp_url: str, wp_username: str, wp_app_password: str,
                       title: str, content: str, featured_media_id: int = None,
                       status: str = "draft", category_id: int = None) -> dict:
    endpoint = f"{wp_url}/wp-json/wp/v2/posts"
    payload = {"title": title, "content": content, "status": status}
    if featured_media_id:
        payload["featured_media"] = featured_media_id
    if category_id:
        payload["categories"] = [category_id]
    response = requests.post(
        endpoint, auth=HTTPBasicAuth(wp_username, wp_app_password),
        json=payload, timeout=30,
    )
    response.raise_for_status()
    return response.json()


# ---------- Facebook Page ----------

def post_to_facebook_page(page_id: str, page_token: str, message: str, image_bytes: bytes = None) -> dict:
    base = f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{page_id}"
    if image_bytes:
        url = f"{base}/photos"
        files = {"source": ("cover.jpg", image_bytes, "image/jpeg")}
        data = {"caption": message, "access_token": page_token}
        response = requests.post(url, data=data, files=files, timeout=60)
    else:
        url = f"{base}/feed"
        data = {"message": message, "access_token": page_token}
        response = requests.post(url, data=data, timeout=30)

    if not response.ok:
        try:
            error_detail = response.json().get("error", {}).get("message", response.text)
        except Exception:
            error_detail = response.text
        raise RuntimeError(f"Facebook báo lỗi: {error_detail}")
    return response.json()


def get_fb_post_permalink(post_id: str, page_token: str) -> str:
    endpoint = f"https://graph.facebook.com/{FB_GRAPH_VERSION}/{post_id}"
    response = requests.get(
        endpoint, params={"fields": "permalink_url", "access_token": page_token}, timeout=20,
    )
    response.raise_for_status()
    permalink = response.json().get("permalink_url", "")
    if permalink.startswith("/"):
        permalink = "https://www.facebook.com" + permalink
    return permalink


# ---------- Google Sheets ----------

def extract_sheet_id(text: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", text)
    if match:
        return match.group(1)
    return text.strip()


def open_user_sheet(sheet_id: str):
    if not GSPREAD_CLIENT:
        raise RuntimeError("Hệ thống chưa được cấu hình Google Sheets (thiếu GOOGLE_SERVICE_ACCOUNT_JSON).")
    sh = GSPREAD_CLIENT.open_by_key(sheet_id)
    ws = sh.sheet1
    header = ws.row_values(1)
    if header[:5] != ["Stt", "Head key", "Title", "Link", "Ngày đăng tải"]:
        ws.update("A1:E1", [["Stt", "Head key", "Title", "Link", "Ngày đăng tải"]])
    return ws


def append_to_sheet(sheet_id: str, headkey: str, title: str, link: str):
    ws = open_user_sheet(sheet_id)
    existing_rows = len(ws.get_all_values()) - 1
    next_stt = max(existing_rows, 0) + 1
    ws.append_row([next_stt, headkey, title, link, datetime.now().strftime("%d/%m/%Y")])


def get_sheet_url(sheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"


# ---------- Quy trình đăng bài đầy đủ (dùng chung cho cả web và Telegram) ----------

def publish_article(website: dict, article: dict, category_id, cover_bytes: bytes, inline_bytes: bytes) -> dict:
    """
    Thực hiện toàn bộ quy trình: upload ảnh, đăng WordPress, ghi Sheet, đăng Facebook.
    website: dict 1 dòng trong bảng "websites" - chứa wp_url, wp_username, wp_app_password, wp_status,
             sheet_id, fb_page_id, fb_page_token, cta_text, cta_url CỦA RIÊNG WEBSITE ĐÓ
             (1 tài khoản có thể có nhiều website, mỗi cái độc lập hoàn toàn).
    Trả về dict kết quả chi tiết để hiển thị cho người dùng.
    """
    wp_url = website["wp_url"]
    wp_username = website["wp_username"]
    wp_app_password = website["wp_app_password"]
    wp_status = website.get("wp_status") or "draft"
    cta_text = website.get("cta_text") or DEFAULT_CTA_TEXT
    cta_url = website.get("cta_url") or wp_url

    slug = slugify(article["title"])
    cover_media = upload_image_to_wordpress(wp_url, wp_username, wp_app_password, cover_bytes, f"{slug}-cover.jpg")
    inline_media = upload_image_to_wordpress(wp_url, wp_username, wp_app_password, inline_bytes, f"{slug}-inline.jpg")

    content_with_image = insert_image_into_content(article["content"], inline_media["source_url"], article["title"])
    content_final = append_cta(content_with_image, cta_text, cta_url)

    result = post_to_wordpress(
        wp_url, wp_username, wp_app_password,
        article["title"], content_final,
        featured_media_id=cover_media["id"], status=wp_status, category_id=category_id,
    )
    link = result.get("link", "")
    post_status = result.get("status", wp_status)

    share_text = build_share_text(article, link)

    out = {
        "link": link,
        "status": post_status,
        "share_text": share_text,
        "sheet_ok": False,
        "sheet_error": None,
        "sheet_url": None,
        "fb_ok": False,
        "fb_error": None,
        "fb_link": None,
        "fb_skipped": True,
        "sheet_skipped": True,
    }

    if website.get("sheet_id"):
        out["sheet_skipped"] = False
        try:
            append_to_sheet(website["sheet_id"], article["headkey"], article["title"], link)
            out["sheet_ok"] = True
            out["sheet_url"] = get_sheet_url(website["sheet_id"])
        except Exception as e:
            out["sheet_error"] = str(e)

    if website.get("fb_page_id") and website.get("fb_page_token"):
        out["fb_skipped"] = False
        try:
            fb_result = post_to_facebook_page(website["fb_page_id"], website["fb_page_token"], share_text, image_bytes=cover_bytes)
            fb_post_id = fb_result.get("post_id") or fb_result.get("id")
            out["fb_ok"] = True
            if fb_post_id:
                try:
                    out["fb_link"] = get_fb_post_permalink(fb_post_id, website["fb_page_token"])
                except Exception:
                    pass
        except Exception as e:
            out["fb_error"] = str(e)

    return out
