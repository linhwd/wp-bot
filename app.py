"""
app.py - Flask Web App + khởi động Bot Telegram ở thread nền (dùng chung 1 database).

Chạy: python app.py
Deploy trên Render: Start Command = python app.py
"""

import os
import logging
import threading
import base64
import json
import random
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()  # Đọc file .env (Gemini key, thông tin ngân hàng, Admin email...) TRƯỚC khi import core/db,
                # vì core.py đọc os.getenv() ngay khi module được nạp lần đầu.

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, jsonify, abort
)

import core
import db

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "doi-chuoi-nay-truoc-khi-len-production")

ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").lower().strip()

db.init_db()

PENDING_ARTICLES = {}


def current_account():
    account_id = session.get("account_id")
    if not account_id:
        return None
    return db.get_account(account_id)


@app.context_processor
def inject_access_context():
    """Bơm 'access' (trạng thái dùng thử/gói) và 'current_plan' (thông tin gói đang dùng)
    vào MỌI template tự động - tránh phải nhớ truyền tay ở từng route (đã từng gây lỗi
    card Premium hiển thị sai lệch ở các trang quên truyền access)."""
    account = current_account()
    if not account:
        return {}
    access = db.account_access_status(account)
    current_plan = None
    if access["active"] and access["source"] == "subscription":
        current_plan = core.PLANS.get(account.get("current_plan_code"))
    return {"access": access, "current_plan": current_plan}


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_account():
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def access_required(view):
    """Chặn các trang/API cần tài khoản đang trong hạn dùng thử HOẶC đã mua gói."""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        account = current_account()
        if not account:
            return redirect(url_for("login", next=request.path))
        status = db.account_access_status(account)
        if not status["active"]:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Bạn cần mua gói để tiếp tục dùng tính năng viết bài.", "need_upgrade": True}), 402
            flash("Bạn đã hết hạn dùng thử/gói. Vui lòng mua gói để tiếp tục.", "error")
            return redirect(url_for("billing"))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        account = current_account()
        if not account or not account.get("is_admin"):
            abort(403)
        return view(*args, **kwargs)

    return wrapped


# ---------- Trang xác thực ----------

@app.route("/")
def index():
    if current_account():
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        display_name = request.form.get("display_name", "").strip()

        if not email or "@" not in email:
            flash("Email không hợp lệ.", "error")
        elif len(password) < 6:
            flash("Mật khẩu cần tối thiểu 6 ký tự.", "error")
        elif password != password2:
            flash("Mật khẩu nhập lại không khớp.", "error")
        elif db.get_account_by_email(email):
            flash("Email này đã có tài khoản rồi. Đăng nhập thay vì đăng ký.", "error")
        else:
            account_id = db.create_account_with_email(email, password, display_name)
            session["account_id"] = account_id
            flash("Tạo tài khoản thành công!", "success")
            return redirect(url_for("dashboard"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        account = db.verify_password(email, password)
        if account:
            if ADMIN_EMAIL and email == ADMIN_EMAIL and not account.get("is_admin"):
                db.update_account(account["id"], is_admin=1)
            session["account_id"] = account["id"]
            return redirect(request.args.get("next") or url_for("dashboard"))
        flash("Sai email hoặc mật khẩu.", "error")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- Dashboard & Cài đặt ----------

@app.route("/dashboard")
@login_required
def dashboard():
    account = current_account()
    access = db.account_access_status(account)

    websites = db.list_websites(account["id"])
    max_sites = db.max_websites(account, core.PLANS, trial_max=core.TRIAL_MAX_SITES)
    posts_published = account.get("posts_published") or 0
    posts_attempts = account.get("posts_attempts") or 0
    success_rate = round((posts_published / posts_attempts) * 100) if posts_attempts else 0
    recent_posts = db.list_recent_posts(account["id"], limit=5)
    category_stats = db.category_breakdown(account["id"], limit=5)
    recent_activity = db.list_recent_activity(account["id"], limit=6)

    return render_template(
        "dashboard.html", account=account, access=access, default_cta=core.DEFAULT_CTA_TEXT,
        stat_posts=posts_published, websites=websites, max_sites=max_sites, stat_success_rate=success_rate,
        recent_posts=recent_posts, category_stats=category_stats, recent_activity=recent_activity,
    )


@app.route("/posts")
@login_required
def posts_history():
    account = current_account()
    per_page = 15
    total = db.count_posts(account["id"])
    total_pages = max(1, (total + per_page - 1) // per_page)

    page = request.args.get("page", 1, type=int) or 1
    page = max(1, min(page, total_pages))

    posts = db.list_posts_paginated(account["id"], page=page, per_page=per_page)
    return render_template(
        "posts_history.html", account=account, posts=posts,
        page=page, total_pages=total_pages, total=total,
    )


@app.route("/settings")
@login_required
def settings():
    account = current_account()
    websites = db.list_websites(account["id"])
    max_sites = db.max_websites(account, core.PLANS, trial_max=core.TRIAL_MAX_SITES)
    return render_template("settings.html", account=account, websites=websites, max_sites=max_sites)


@app.route("/settings/websites/new", methods=["POST"])
@login_required
def create_website():
    account = current_account()
    max_sites = db.max_websites(account, core.PLANS, trial_max=core.TRIAL_MAX_SITES)
    current_count = db.count_websites(account["id"])
    if max_sites is not None and current_count >= max_sites:
        flash(f"Gói của bạn chỉ được kết nối tối đa {max_sites} website. Hãy nâng cấp gói để thêm.", "error")
        return redirect(url_for("settings"))

    website_id = db.create_website(account["id"], label=f"Website {current_count + 1}")
    return redirect(url_for("edit_website", website_id=website_id))


@app.route("/settings/websites/<int:website_id>", methods=["GET", "POST"])
@login_required
def edit_website(website_id):
    account = current_account()
    website = db.get_website(website_id, account["id"])
    if not website:
        abort(404)

    if request.method == "POST":
        section = request.form.get("section")

        if section == "label":
            label = request.form.get("label", "").strip()
            db.update_website(website_id, account["id"], label=label or None)
            flash("Đã lưu tên website.", "success")

        elif section == "wordpress":
            wp_url = request.form.get("wp_url", "").strip().rstrip("/")
            wp_username = request.form.get("wp_username", "").strip()
            wp_app_password = request.form.get("wp_app_password", "").strip()
            wp_status = request.form.get("wp_status", "draft")

            # Ô Application Password chỉ hiện placeholder, để trống nghĩa là không đổi - giữ mật khẩu cũ.
            if not wp_app_password and website.get("wp_app_password"):
                wp_app_password = website["wp_app_password"]

            if wp_url and wp_username and wp_app_password:
                db.update_website(
                    website_id, account["id"], wp_url=wp_url, wp_username=wp_username,
                    wp_app_password=wp_app_password, wp_status=wp_status,
                )
                flash("Đã lưu cấu hình WordPress.", "success")
            else:
                flash("Cần điền đủ Link web, Username và Application Password.", "error")

        elif section == "sheet":
            sheet_link = request.form.get("sheet_link", "").strip()
            if sheet_link:
                sheet_id = core.extract_sheet_id(sheet_link)
                try:
                    core.open_user_sheet(sheet_id)
                    db.update_website(website_id, account["id"], sheet_id=sheet_id)
                    flash("Đã kết nối Google Sheet.", "success")
                except Exception as e:
                    flash(f"Không mở được Sheet. Đã share cho email {core.SERVICE_ACCOUNT_EMAIL} chưa? Chi tiết: {e}", "error")
            else:
                db.update_website(website_id, account["id"], sheet_id=None)
                flash("Đã gỡ kết nối Google Sheet.", "success")

        elif section == "facebook":
            fb_page_id = request.form.get("fb_page_id", "").strip()
            fb_page_token = request.form.get("fb_page_token", "").strip()

            if not fb_page_token and website.get("fb_page_token"):
                fb_page_token = website["fb_page_token"]

            db.update_website(website_id, account["id"], fb_page_id=fb_page_id or None, fb_page_token=fb_page_token or None)
            flash("Đã lưu cấu hình Facebook Page.", "success")

        elif section == "cta":
            cta_text = request.form.get("cta_text", "").strip()
            cta_url = request.form.get("cta_url", "").strip()
            db.update_website(website_id, account["id"], cta_text=cta_text or None, cta_url=cta_url or None)
            flash("Đã lưu câu CTA.", "success")

        return redirect(url_for("edit_website", website_id=website_id))

    website = db.get_website(website_id, account["id"])
    return render_template("website_edit.html", account=account, website=website,
                            default_cta=core.DEFAULT_CTA_TEXT, service_account_email=core.SERVICE_ACCOUNT_EMAIL,
                            sheets_enabled=bool(core.GSPREAD_CLIENT))


@app.route("/settings/websites/<int:website_id>/delete", methods=["POST"])
@login_required
def delete_website(website_id):
    account = current_account()
    website = db.get_website(website_id, account["id"])
    if not website:
        abort(404)
    db.delete_website(website_id, account["id"])
    flash("Đã xoá website.", "success")
    return redirect(url_for("settings"))


# ---------- Viết bài (trang + API) ----------

@app.route("/write")
@login_required
def write_page():
    account = current_account()
    access = db.account_access_status(account)
    websites = db.list_websites(account["id"])
    ready = bool(websites) and access["active"]
    max_slots = db.max_write_slots(account, core.PLANS, trial_slots=core.TRIAL_WRITE_SLOTS)
    return render_template("write.html", account=account, ready=ready, access=access,
                            wp_connected=bool(websites), websites=websites, max_slots=max_slots)


@app.route("/api/write/generate", methods=["POST"])
@login_required
@access_required
def api_generate():
    account = current_account()
    body = request.json or {}
    topic = body.get("topic", "").strip()
    slot_id = str(body.get("slot_id") or "s1")
    website_id = body.get("website_id")
    website_ids_raw = body.get("website_ids")  # list - chế độ đăng đồng thời nhiều website

    categories_by_website = {}

    if website_ids_raw:
        website_ids = []
        for wid in website_ids_raw:
            w = db.get_website(int(wid), account["id"])
            if w and w.get("wp_url"):
                website_ids.append(w["id"])
                # Đa website: lấy chuyên mục RIÊNG cho từng site, vì mỗi WordPress có hệ
                # chuyên mục (category id) hoàn toàn khác nhau - không dùng chung 1 danh sách.
                try:
                    cats = core.get_wp_categories(w["wp_url"], w["wp_username"], w["wp_app_password"])
                except Exception:
                    cats = []
                categories_by_website[str(w["id"])] = cats
        if not website_ids:
            return jsonify({"error": "Vui lòng chọn ít nhất 1 website đã cấu hình đầy đủ WordPress."}), 400
        categories = []
    else:
        website = db.get_website(int(website_id), account["id"]) if website_id else None
        if not website:
            websites = db.list_websites(account["id"])
            website = websites[0] if websites else None
        if not website or not website.get("wp_url"):
            return jsonify({"error": "Bạn cần kết nối ít nhất 1 website WordPress trong phần Cài đặt trước."}), 400
        website_ids = [website["id"]]
        try:
            categories = core.get_wp_categories(website["wp_url"], website["wp_username"], website["wp_app_password"])
        except Exception:
            categories = []

    if not topic:
        return jsonify({"error": "Vui lòng nhập chủ đề bài viết."}), 400

    account_slots = PENDING_ARTICLES.setdefault(account["id"], {})
    max_slots = db.max_write_slots(account, core.PLANS, trial_slots=core.TRIAL_WRITE_SLOTS)
    if slot_id not in account_slots and len(account_slots) >= max_slots:
        return jsonify({
            "error": f"Gói của bạn chỉ được soạn tối đa {max_slots} bài cùng lúc. "
                     f"Hãy hoàn tất hoặc hủy bớt bài đang soạn trước khi mở thêm."
        }), 400

    try:
        article = core.generate_article(topic)
    except Exception as e:
        return jsonify({"error": f"Có lỗi khi viết bài: {e}"}), 500

    account_slots[slot_id] = {
        "article": article, "topic": topic, "website_ids": website_ids,
        "categories": categories, "categories_by_website": categories_by_website,
    }

    return jsonify({
        "headkey": article["headkey"],
        "title": article["title"],
        "preview": core.html_to_preview_text(article["content"]),
        "content_html": article["content"],
        "categories": categories,
        "categories_by_website": categories_by_website,
        "website_ids": website_ids,
        "multi": len(website_ids) > 1,
    })


@app.route("/api/write/rewrite", methods=["POST"])
@login_required
@access_required
def api_rewrite():
    account = current_account()
    body = request.json or {}
    slot_id = str(body.get("slot_id") or "s1")
    pending = PENDING_ARTICLES.get(account["id"], {}).get(slot_id)
    if not pending:
        return jsonify({"error": "Không tìm thấy bài viết đang soạn. Hãy nhập chủ đề mới."}), 400

    try:
        article = core.generate_article(pending["topic"])
    except Exception as e:
        return jsonify({"error": f"Có lỗi khi viết bài: {e}"}), 500

    pending["article"] = article
    return jsonify({
        "headkey": article["headkey"],
        "title": article["title"],
        "preview": core.html_to_preview_text(article["content"]),
        "content_html": article["content"],
    })


@app.route("/api/write/generate_batch", methods=["POST"])
@login_required
@access_required
def api_generate_batch():
    """Chế độ 'mỗi web 1 bài riêng' - 1 lượt gọi này tạo N bài viết KHÁC NHAU cho N website,
    tất cả gộp trong CÙNG 1 slot (1 tab), không tách thành nhiều slot riêng lẻ."""
    account = current_account()
    body = request.json or {}
    slot_id = str(body.get("slot_id") or "s1")
    pairs = body.get("pairs") or []  # [{"website_id": "12", "topic": "..."}]

    if not pairs:
        return jsonify({"error": "Vui lòng chọn ít nhất 1 website và nhập chủ đề."}), 400

    account_slots = PENDING_ARTICLES.setdefault(account["id"], {})
    max_slots = db.max_write_slots(account, core.PLANS, trial_slots=core.TRIAL_WRITE_SLOTS)
    if slot_id not in account_slots and len(account_slots) >= max_slots:
        return jsonify({
            "error": f"Gói của bạn chỉ được soạn tối đa {max_slots} bài cùng lúc. "
                     f"Hãy hoàn tất hoặc hủy bớt bài đang soạn trước khi mở thêm."
        }), 400

    website_ids = []
    articles = {}
    categories_by_website = {}
    warnings = []

    for pair in pairs:
        wid_raw = pair.get("website_id")
        topic = (pair.get("topic") or "").strip()
        website = db.get_website(int(wid_raw), account["id"]) if wid_raw else None
        if not website or not website.get("wp_url") or not topic:
            warnings.append(f"Bỏ qua 1 website: thiếu chủ đề hoặc chưa cấu hình WordPress.")
            continue
        wid = website["id"]
        try:
            article = core.generate_article(topic)
        except Exception as e:
            warnings.append(f"{website.get('label') or ('Website ' + str(wid))}: lỗi viết bài - {e}")
            continue
        try:
            cats = core.get_wp_categories(website["wp_url"], website["wp_username"], website["wp_app_password"])
        except Exception:
            cats = []
        website_ids.append(wid)
        articles[str(wid)] = {"article": article, "topic": topic}
        categories_by_website[str(wid)] = cats

    if not website_ids:
        return jsonify({"error": "Không tạo được bài viết nào. " + " ".join(warnings)}), 400

    account_slots[slot_id] = {
        "mode": "separate",
        "website_ids": website_ids,
        "articles": articles,
        "categories_by_website": categories_by_website,
    }

    return jsonify({
        "website_ids": website_ids,
        "items": [
            {
                "website_id": wid,
                "website_label": db.get_website(wid, account["id"]).get("label") or f"Website {wid}",
                "headkey": articles[str(wid)]["article"]["headkey"],
                "title": articles[str(wid)]["article"]["title"],
                "preview": core.html_to_preview_text(articles[str(wid)]["article"]["content"]),
                "content_html": articles[str(wid)]["article"]["content"],
            }
            for wid in website_ids
        ],
        "categories_by_website": categories_by_website,
        "warnings": warnings,
    })


@app.route("/api/write/rewrite_batch_item", methods=["POST"])
@login_required
@access_required
def api_rewrite_batch_item():
    account = current_account()
    body = request.json or {}
    slot_id = str(body.get("slot_id") or "s1")
    website_id = str(body.get("website_id") or "")
    pending = PENDING_ARTICLES.get(account["id"], {}).get(slot_id)
    if not pending or pending.get("mode") != "separate":
        return jsonify({"error": "Không tìm thấy bài viết đang soạn."}), 400

    item = pending.get("articles", {}).get(website_id)
    if not item:
        return jsonify({"error": "Không tìm thấy bài viết cho website này."}), 400

    try:
        article = core.generate_article(item["topic"])
    except Exception as e:
        return jsonify({"error": f"Có lỗi khi viết lại bài: {e}"}), 500

    item["article"] = article
    return jsonify({
        "headkey": article["headkey"],
        "title": article["title"],
        "preview": core.html_to_preview_text(article["content"]),
        "content_html": article["content"],
    })


@app.route("/api/write/auto_image", methods=["POST"])
@login_required
@access_required
def api_auto_image():
    account = current_account()
    body = request.json or {}
    slot_id = str(body.get("slot_id") or "s1")
    website_id = body.get("website_id")  # chỉ có khi ở chế độ "mỗi web 1 bài riêng"
    pending = PENDING_ARTICLES.get(account["id"], {}).get(slot_id)
    if not pending:
        return jsonify({"error": "Không tìm thấy bài viết đang soạn."}), 400

    if website_id is not None:
        item = pending.get("articles", {}).get(str(website_id))
        if not item:
            return jsonify({"error": "Không tìm thấy bài viết cho website này."}), 400
        query = item["article"].get("headkey") or item["topic"]
    else:
        query = pending["article"].get("headkey") or pending["topic"]

    try:
        cover_bytes, inline_bytes, prompt = core.auto_images_for_topic(query)
    except Exception as e:
        return jsonify({"error": f"Không tự tạo được ảnh phù hợp: {e}"}), 500

    # Lưu lại prompt đã dùng - để nút "Đổi ảnh khác" tái sử dụng, không cần dịch lại qua Gemini mỗi lần.
    if website_id is not None:
        pending.setdefault("image_prompt_by_site", {})[str(website_id)] = prompt
    else:
        pending["image_prompt"] = prompt

    return jsonify({
        "cover_base64": base64.b64encode(cover_bytes).decode("ascii"),
        "inline_base64": base64.b64encode(inline_bytes).decode("ascii"),
    })


@app.route("/api/write/reroll_image", methods=["POST"])
@login_required
@access_required
def api_reroll_image():
    account = current_account()
    body = request.json or {}
    slot_id = str(body.get("slot_id") or "s1")
    which = body.get("which")
    website_id = body.get("website_id")
    if which not in ("cover", "inline"):
        return jsonify({"error": "Yêu cầu không hợp lệ."}), 400

    pending = PENDING_ARTICLES.get(account["id"], {}).get(slot_id)
    if not pending:
        return jsonify({"error": "Không tìm thấy bài viết đang soạn."}), 400

    if website_id is not None:
        wid_key = str(website_id)
        prompt = pending.get("image_prompt_by_site", {}).get(wid_key)
        if not prompt:
            item = pending.get("articles", {}).get(wid_key)
            if not item:
                return jsonify({"error": "Không tìm thấy bài viết cho website này."}), 400
            query = item["article"].get("headkey") or item["topic"]
            prompt = core.translate_to_image_query(query)
            pending.setdefault("image_prompt_by_site", {})[wid_key] = prompt
    else:
        prompt = pending.get("image_prompt")
        if not prompt:
            query = pending["article"].get("headkey") or pending["topic"]
            prompt = core.translate_to_image_query(query)
            pending["image_prompt"] = prompt

    # Mỗi lần đổi ảnh chỉ cần seed ngẫu nhiên mới - Pollinations tự ra ảnh khác dù cùng 1 prompt,
    # không cần tìm/tải lại cả danh sách như trước.
    width, height = (1200, 630) if which == "cover" else (1000, 750)
    try:
        photo_bytes = core.generate_pollinations_image(prompt, width=width, height=height)
    except Exception as e:
        return jsonify({"error": f"Không tạo được ảnh: {e}"}), 500

    return jsonify({"image_base64": base64.b64encode(photo_bytes).decode("ascii")})


@app.route("/api/write/publish", methods=["POST"])
@login_required
@access_required
def api_publish():
    account = current_account()
    slot_id = str(request.form.get("slot_id") or "s1")
    pending = PENDING_ARTICLES.get(account["id"], {}).get(slot_id)
    if not pending:
        return jsonify({"error": "Không tìm thấy bài viết đang soạn. Hãy nhập chủ đề mới."}), 400

    website_ids = pending.get("website_ids") or []
    if not website_ids:
        return jsonify({"error": "Không xác định được website để đăng bài."}), 400

    is_separate = pending.get("mode") == "separate"
    is_multi = len(website_ids) > 1

    # Đa website CÙNG nội dung: 1 bộ ảnh chung. Chế độ "mỗi web 1 bài riêng": mỗi web 1 bộ ảnh riêng
    # (đọc bên trong vòng lặp, key dạng cover_<id>/inline_<id>).
    cover_bytes = inline_bytes = None
    if not is_separate:
        cover_file = request.files.get("cover")
        inline_file = request.files.get("inline")
        if not cover_file or not inline_file:
            return jsonify({"error": "Cần đủ 2 ảnh: ảnh bìa và ảnh trong bài."}), 400
        # Đọc bytes 1 LẦN duy nhất - vì phải upload cùng 1 ảnh cho nhiều website khi đăng đồng thời,
        # trong khi file stream chỉ .read() được 1 lần.
        cover_bytes = cover_file.read()
        inline_bytes = inline_file.read()

    category_id = request.form.get("category_id")
    category_id = int(category_id) if category_id and category_id != "skip" else None

    # Đa website: category_map là JSON {"12": "5", "13": "skip"} - mỗi website 1 lựa chọn chuyên mục riêng.
    category_map_raw = request.form.get("category_map")
    category_map = {}
    if category_map_raw:
        try:
            category_map = json.loads(category_map_raw)
        except Exception:
            category_map = {}

    results = []
    for wid in website_ids:
        website = db.get_website(wid, account["id"])
        if not website:
            db.record_publish_attempt(account["id"], success=False)
            results.append({"website_id": wid, "website_label": "Website đã xoá", "ok": False,
                             "error": "Website không còn tồn tại (có thể đã bị xoá)."})
            continue

        # Xác định bài viết + chuyên mục + ảnh dùng cho ĐÚNG website này
        if is_separate:
            item = pending.get("articles", {}).get(str(wid))
            if not item:
                results.append({"website_id": wid, "website_label": website.get("label") or f"Website {wid}",
                                 "ok": False, "error": "Không tìm thấy bài viết cho website này."})
                continue
            article_for_site = item["article"]

            site_cover_file = request.files.get(f"cover_{wid}")
            site_inline_file = request.files.get(f"inline_{wid}")
            if not site_cover_file or not site_inline_file:
                results.append({"website_id": wid, "website_label": website.get("label") or f"Website {wid}",
                                 "ok": False, "error": "Thiếu ảnh bìa/ảnh trong bài cho website này."})
                continue
            site_cover_bytes = site_cover_file.read()
            site_inline_bytes = site_inline_file.read()
        else:
            article_for_site = pending["article"]
            site_cover_bytes = cover_bytes
            site_inline_bytes = inline_bytes

        if is_multi:
            raw_cat = category_map.get(str(wid))
            cat_id_for_site = int(raw_cat) if raw_cat and raw_cat != "skip" else None
            cat_name_for_site = None
            if cat_id_for_site:
                for c in pending.get("categories_by_website", {}).get(str(wid), []):
                    if c.get("id") == cat_id_for_site:
                        cat_name_for_site = c.get("name")
                        break
        else:
            cat_id_for_site = category_id
            cat_name_for_site = None
            if cat_id_for_site:
                for c in pending.get("categories", []):
                    if c.get("id") == cat_id_for_site:
                        cat_name_for_site = c.get("name")
                        break

        try:
            result = core.publish_article(website, article_for_site, cat_id_for_site, site_cover_bytes, site_inline_bytes)
            db.record_publish_attempt(account["id"], success=True)
            db.record_post(
                account["id"], title=article_for_site["title"], link=result.get("link"),
                category=cat_name_for_site, status=result.get("status"),
            )
            result["website_id"] = wid
            result["website_label"] = website.get("label") or f"Website {wid}"
            result["ok"] = True
            results.append(result)
        except Exception as e:
            logger.exception("Lỗi khi đăng bài lên website %s", wid)
            db.record_publish_attempt(account["id"], success=False)
            results.append({"website_id": wid, "website_label": website.get("label") or f"Website {wid}",
                             "ok": False, "error": str(e)})

    PENDING_ARTICLES.get(account["id"], {}).pop(slot_id, None)

    if is_multi:
        return jsonify({"multi": True, "results": results})

    # Chế độ 1 website: giữ đúng format cũ để không phá vỡ gì khác đang phụ thuộc vào nó
    r = results[0]
    if not r.get("ok"):
        return jsonify({"error": r.get("error", "Có lỗi khi đăng bài")}), 500
    return jsonify(r)


@app.route("/api/write/cancel", methods=["POST"])
@login_required
def api_cancel():
    account = current_account()
    slot_id = str((request.json or {}).get("slot_id") or "s1")
    PENDING_ARTICLES.get(account["id"], {}).pop(slot_id, None)
    return jsonify({"ok": True})


# ---------- Bảng giá & Mua gói ----------

@app.route("/pricing")
def pricing():
    account = current_account()
    access = db.account_access_status(account) if account else None
    is_first_order = bool(account) and not db.has_any_paid_order(account["id"])
    return render_template("pricing.html", account=account, access=access,
                            plans=core.PLANS, is_first_order=is_first_order, format_vnd=core.format_vnd,
                            monthly_eq=core.plan_monthly_equivalent, savings_pct=core.plan_savings_pct)


@app.route("/billing")
@login_required
def billing():
    account = current_account()
    access = db.account_access_status(account)
    orders = db.list_orders_for_account(account["id"])
    is_first_order = not db.has_any_paid_order(account["id"])
    return render_template("billing.html", account=account, access=access, orders=orders,
                            plans=core.PLANS, is_first_order=is_first_order, format_vnd=core.format_vnd,
                            monthly_eq=core.plan_monthly_equivalent, savings_pct=core.plan_savings_pct)


@app.route("/checkout/<plan_code>", methods=["POST"])
@login_required
def checkout(plan_code):
    account = current_account()
    if plan_code not in core.PLANS:
        abort(404)
    plan = core.PLANS[plan_code]
    is_first_order = not db.has_any_paid_order(account["id"])
    amount = core.plan_price_for(plan_code, is_first_order)
    order = db.create_order(account["id"], plan_code, plan["months"], amount, is_first_order)
    return redirect(url_for("view_order", order_id=order["id"]))


@app.route("/orders/<int:order_id>")
@login_required
def view_order(order_id):
    account = current_account()
    order = db.get_order(order_id)
    if not order or order["account_id"] != account["id"]:
        abort(404)
    qr_url = core.build_vietqr_url(order["amount"], order["order_code"])
    bank_configured = bool(core.VIETQR_BANK_ID and core.VIETQR_ACCOUNT_NO)
    return render_template("order.html", order=order, qr_url=qr_url, bank_configured=bank_configured,
                            bank_account_no=core.VIETQR_ACCOUNT_NO, bank_account_name=core.VIETQR_ACCOUNT_NAME,
                            format_vnd=core.format_vnd, plan=core.PLANS.get(order["plan_code"]))


# ---------- Trang Admin ----------

@app.route("/admin")
@login_required
@admin_required
def admin_dashboard():
    pending_orders = db.list_orders(status="pending")
    return render_template("admin_orders.html", orders=pending_orders, format_vnd=core.format_vnd, filter_status="pending")


@app.route("/admin/orders")
@login_required
@admin_required
def admin_orders_all():
    all_orders = db.list_orders()
    return render_template("admin_orders.html", orders=all_orders, format_vnd=core.format_vnd, filter_status=None)


@app.route("/admin/orders/<int:order_id>/approve", methods=["POST"])
@login_required
@admin_required
def admin_approve_order(order_id):
    order = db.get_order(order_id)
    if not order:
        abort(404)
    db.update_order_status(order_id, "paid")
    new_until = db.extend_subscription(order["account_id"], order["months"], plan_code=order["plan_code"])
    flash(f"Đã duyệt đơn {order['order_code']} - gia hạn tới {new_until.strftime('%d/%m/%Y')}.", "success")
    return redirect(request.referrer or url_for("admin_dashboard"))


@app.route("/admin/orders/<int:order_id>/reject", methods=["POST"])
@login_required
@admin_required
def admin_reject_order(order_id):
    order = db.get_order(order_id)
    if not order:
        abort(404)
    db.update_order_status(order_id, "rejected")
    flash(f"Đã từ chối đơn {order['order_code']}.", "success")
    return redirect(request.referrer or url_for("admin_dashboard"))


# ---------- Health-check (đồng thời phục vụ UptimeRobot nếu dùng gói Free của Render) ----------

@app.route("/healthz")
def healthz():
    return "OK", 200


# ---------- Khởi động bot Telegram ở thread nền ----------

def start_telegram_bot_thread():
    import bot_runtime

    def runner():
        try:
            bot_runtime.run_bot_blocking()
        except Exception:
            logger.exception("Bot Telegram dừng do lỗi")

    threading.Thread(target=runner, daemon=True).start()


if __name__ == "__main__":
    start_telegram_bot_thread()
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port)
