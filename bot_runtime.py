"""
bot_runtime.py - Bot Telegram, dùng lại toàn bộ logic trong core.py và lưu dữ liệu
vào CHUNG 1 database (bảng accounts) với Web App, thông qua db.py.

Được khởi động ở 1 thread nền bởi app.py, chạy song song với Flask.
"""

import os
import html
import logging
import asyncio
from urllib.parse import urlencode

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import core
import db

logger = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")


def build_preview_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Xác nhận & gửi ảnh", callback_data="confirm"),
                InlineKeyboardButton("🔄 Viết lại", callback_data="rewrite"),
            ],
            [InlineKeyboardButton("❌ Hủy", callback_data="cancel")],
        ]
    )


def build_preview_text(article: dict) -> str:
    preview = html.escape(core.html_to_preview_text(article["content"]))
    if len(preview) > 700:
        preview = preview[:700] + "...\n\n[nội dung được rút gọn khi xem trước]"
    return (
        f"📝 <b>{html.escape(article['title'])}</b>\n"
        f"🔑 Head key: {html.escape(article['headkey'])}\n\n"
        f"{preview}"
    )


def reset_state(context: ContextTypes.DEFAULT_TYPE):
    context.user_data["state"] = "idle"
    context.user_data.pop("pending_article", None)
    context.user_data.pop("cover_image", None)
    context.user_data.pop("category_id", None)
    context.user_data.pop("categories_cache", None)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_state(context)
    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    access = db.account_access_status(account)
    sheet_line = (
        f"\n\nĐể dùng Google Sheet, hãy share Sheet của bạn (quyền Editor) cho email:\n{core.SERVICE_ACCOUNT_EMAIL}"
        if core.SERVICE_ACCOUNT_EMAIL else "\n\n(Tính năng Google Sheet hiện chưa được bật.)"
    )
    web_hint = ""
    if account.get("email"):
        web_hint = f"\n\n🌐 Tài khoản Telegram này đã liên kết với web: {account['email']}"
    else:
        web_hint = (
            "\n\n💡 Mẹo: bạn cũng có thể quản lý mọi thứ này trên giao diện Web đẹp hơn, và mua gói tại đó. "
            "Gõ /link_web <email> | <mật khẩu mới> để tạo/liên kết tài khoản web cho đúng Telegram này."
        )

    if access["active"]:
        if access["source"] == "trial":
            access_line = f"🎁 Bạn đang dùng thử miễn phí, hết hạn lúc {access['expires_at'].strftime('%H:%M %d/%m/%Y')}.\n\n"
        else:
            access_line = f"✅ Gói của bạn còn hạn tới {access['expires_at'].strftime('%d/%m/%Y')}.\n\n"
    else:
        access_line = "⚠️ Bạn đã hết hạn dùng thử/gói. Gõ /upgrade để xem các gói và mua thêm.\n\n"

    await update.message.reply_text(
        "👋 Xin chào! Đây là bot viết bài AI tự động và đăng lên WordPress của riêng bạn.\n\n"
        f"{access_line}"
        "🔧 BƯỚC 1 - Kết nối WordPress (bắt buộc):\n"
        "/connect_wp <link web> | <username> | <application password>\n\n"
        "📊 BƯỚC 2 - Kết nối Google Sheet (tùy chọn):\n"
        "/connect_sheet <link Google Sheet>"
        f"{sheet_line}\n\n"
        "📘 BƯỚC 3 - Kết nối Facebook Page (tùy chọn):\n"
        "/connect_facebook <page_id> | <access_token>\n\n"
        "Sau khi kết nối WordPress, chỉ cần nhắn chủ đề bài viết là bắt đầu viết bài!\n\n"
        "Gõ /status để xem cấu hình hiện tại, /upgrade để xem gói, /cancel để hủy quy trình đang làm dở."
        f"{web_hint}"
    )


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_state(context)
    await update.message.reply_text("Đã hủy. Bạn có thể gửi chủ đề bài viết mới bất cứ lúc nào.")


async def link_web_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Liên kết (hoặc tạo mới) tài khoản Web cho đúng Telegram này, để dùng chung dữ liệu ở cả 2 nơi."""
    args_text = update.message.text.partition(" ")[2].strip()
    if not args_text or "|" not in args_text:
        await update.message.reply_text(
            "Cách dùng: /link_web <email> | <mật khẩu bạn muốn đặt>\n\n"
            "Nếu email chưa có tài khoản web, hệ thống sẽ tự tạo mới và liên kết luôn với Telegram này."
        )
        return
    email, password = [p.strip() for p in args_text.split("|", 1)]
    if not email or "@" not in email or len(password) < 6:
        await update.message.reply_text("Email không hợp lệ hoặc mật khẩu quá ngắn (tối thiểu 6 ký tự).")
        return

    telegram_account = db.get_or_create_account_by_telegram(update.effective_user.id)
    existing_email_account = db.get_account_by_email(email)

    if existing_email_account and existing_email_account["id"] != telegram_account["id"]:
        await update.message.reply_text(
            "Email này đã có tài khoản web khác rồi, không liên kết được. "
            "Dùng email khác hoặc đăng nhập web bằng email đó."
        )
        return

    from werkzeug.security import generate_password_hash
    db.update_account(telegram_account["id"], email=email.lower().strip(), password_hash=generate_password_hash(password))
    await update.message.reply_text(
        f"✅ Đã liên kết! Giờ bạn có thể đăng nhập web bằng email {email} với mật khẩu vừa đặt, "
        f"dữ liệu sẽ giống hệt trên Telegram này."
    )


async def upgrade_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    access = db.account_access_status(account)
    site_url = os.getenv("SITE_URL", "")
    lines = []
    if access["active"]:
        if access["source"] == "trial":
            lines.append(f"🎁 Bạn đang dùng thử miễn phí, hết hạn lúc {access['expires_at'].strftime('%H:%M %d/%m/%Y')}.")
        else:
            lines.append(f"✅ Gói của bạn còn hạn tới {access['expires_at'].strftime('%d/%m/%Y')}.")
    else:
        lines.append("⚠️ Bạn đã hết hạn dùng thử/gói.")

    lines.append("\n📦 Bảng giá:")
    for code, plan in core.PLANS.items():
        lines.append(f"- {plan['label']}: {core.format_vnd(plan['price'])}")
    lines.append(f"\nMua gói tại website: {site_url or '(chủ bot chưa cấu hình SITE_URL)'}/pricing")
    await update.message.reply_text("\n".join(lines))


async def connect_wp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args_text = update.message.text.partition(" ")[2].strip()
    if not args_text or "|" not in args_text:
        await update.message.reply_text(
            "Cách dùng:\n/connect_wp <link web> | <username> | <application password>\n\n"
            "Lưu ý: đây là Application Password (WP Admin → Users → Profile → Application Passwords)."
        )
        return
    parts = [p.strip() for p in args_text.split("|")]
    if len(parts) != 3:
        await update.message.reply_text("Thiếu thông tin. Cần đủ 3 phần: link web | username | application password")
        return
    wp_url, wp_username, wp_app_password = parts
    wp_url = wp_url.rstrip("/")
    if not wp_url.startswith("http"):
        await update.message.reply_text("Link web phải bắt đầu bằng http:// hoặc https://")
        return

    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    db.update_account(account["id"], wp_url=wp_url, wp_username=wp_username, wp_app_password=wp_app_password)
    await update.message.reply_text(f"✅ Đã kết nối WordPress: {wp_url}")


async def connect_sheet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not core.GSPREAD_CLIENT:
        await update.message.reply_text("Hệ thống chưa bật tính năng Google Sheets.")
        return
    args_text = update.message.text.partition(" ")[2].strip()
    if not args_text:
        await update.message.reply_text(
            "Cách dùng: /connect_sheet <link Google Sheet>\n\n"
            f"Share Sheet cho email sau với quyền Editor:\n{core.SERVICE_ACCOUNT_EMAIL}"
        )
        return
    sheet_id = core.extract_sheet_id(args_text)
    try:
        core.open_user_sheet(sheet_id)
    except Exception as e:
        await update.message.reply_text(
            f"Không mở được Sheet này. Hãy Share Sheet cho email:\n{core.SERVICE_ACCOUNT_EMAIL}\n\nChi tiết: {e}"
        )
        return

    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    db.update_account(account["id"], sheet_id=sheet_id)
    await update.message.reply_text("✅ Đã kết nối Google Sheet!")


async def connect_facebook_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args_text = update.message.text.partition(" ")[2].strip()
    if not args_text or "|" not in args_text:
        await update.message.reply_text("Cách dùng: /connect_facebook <page_id> | <access_token>")
        return
    parts = [p.strip() for p in args_text.split("|")]
    if len(parts) != 2:
        await update.message.reply_text("Thiếu thông tin. Cần đủ 2 phần: page_id | access_token")
        return
    fb_page_id, fb_page_token = parts
    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    db.update_account(account["id"], fb_page_id=fb_page_id, fb_page_token=fb_page_token)
    await update.message.reply_text("✅ Đã kết nối Facebook Page!")


async def set_cta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args_text = update.message.text.partition(" ")[2].strip()
    if not args_text:
        await update.message.reply_text("Cách dùng: /set_cta <nội dung CTA> | <link>")
        return
    if "|" in args_text:
        cta_text, cta_url = [p.strip() for p in args_text.split("|", 1)]
    else:
        cta_text, cta_url = args_text.strip(), None
    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    db.update_account(account["id"], cta_text=cta_text, cta_url=cta_url)
    await update.message.reply_text("✅ Đã lưu câu CTA mới.")


async def set_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args_text = update.message.text.partition(" ")[2].strip().lower()
    if args_text not in ("draft", "publish"):
        await update.message.reply_text("Cách dùng: /set_status draft   hoặc   /set_status publish")
        return
    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    db.update_account(account["id"], wp_status=args_text)
    await update.message.reply_text(f"✅ Đã đặt trạng thái đăng bài mặc định: {args_text}")


async def sheet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    if account.get("sheet_id"):
        await update.message.reply_text(f"📄 Google Sheet của bạn:\n{core.get_sheet_url(account['sheet_id'])}")
    else:
        await update.message.reply_text("Bạn chưa kết nối Google Sheet. Gõ /connect_sheet <link> để kết nối.")


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    access = db.account_access_status(account)
    if access["active"]:
        access_text = (
            f"🎁 Dùng thử tới {access['expires_at'].strftime('%H:%M %d/%m/%Y')}"
            if access["source"] == "trial"
            else f"✅ Gói còn hạn tới {access['expires_at'].strftime('%d/%m/%Y')}"
        )
    else:
        access_text = "⚠️ Đã hết hạn - gõ /upgrade để mua gói"
    text = (
        f"📦 Trạng thái gói: {access_text}\n\n"
        f"🌐 WordPress: {account.get('wp_url') or 'chưa kết nối'}\n"
        f"📌 Trạng thái đăng bài mặc định: {account.get('wp_status') or 'draft'}\n\n"
        f"📊 Google Sheet: {'đã kết nối' if account.get('sheet_id') else 'chưa kết nối'}\n"
        f"📘 Facebook Page: {('đã kết nối (ID: ' + account['fb_page_id'] + ')') if account.get('fb_page_id') else 'chưa kết nối'}\n\n"
        f"📣 CTA: {account.get('cta_text') or f'(mặc định: {core.DEFAULT_CTA_TEXT})'}\n\n"
        f"🌐 Tài khoản Web liên kết: {account.get('email') or 'chưa liên kết (dùng /link_web để tạo)'}"
    )
    await update.message.reply_text(text)


async def handle_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state", "idle")
    if state in ("awaiting_cover", "awaiting_inline"):
        label = "ẢNH BÌA" if state == "awaiting_cover" else "ẢNH TRONG BÀI VIẾT"
        await update.message.reply_text(f"Mình đang chờ bạn gửi {label}. Gõ /cancel nếu muốn hủy.")
        return

    account = db.get_or_create_account_by_telegram(update.effective_user.id)

    access = db.account_access_status(account)
    if not access["active"]:
        await update.message.reply_text("⚠️ Bạn đã hết hạn dùng thử/gói. Gõ /upgrade để xem các gói và mua thêm.")
        return

    if not account.get("wp_url"):
        await update.message.reply_text(
            "Bạn cần kết nối WordPress trước:\n"
            "/connect_wp <link web> | <username> | <application password>\n\nGõ /start để xem hướng dẫn."
        )
        return

    topic = update.message.text.strip()
    if not topic:
        return

    status_msg = await update.message.reply_text("Đang viết bài, chờ mình chút... ✍️")
    try:
        article = core.generate_article(topic)
    except Exception as e:
        logger.exception("Lỗi khi gọi Gemini")
        await status_msg.edit_text(f"Có lỗi khi viết bài: {e}")
        return

    context.user_data["pending_article"] = article
    context.user_data["topic"] = topic
    context.user_data["state"] = "preview"
    await status_msg.edit_text(build_preview_text(article), parse_mode="HTML", reply_markup=build_preview_keyboard())


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    account = db.get_or_create_account_by_telegram(update.effective_user.id)
    article = context.user_data.get("pending_article")
    topic = context.user_data.get("topic")

    if query.data == "cancel":
        reset_state(context)
        await query.edit_message_text("Đã hủy bài viết.")
        return

    if query.data == "rewrite":
        if not topic:
            await query.edit_message_text("Không tìm thấy chủ đề, hãy gửi lại chủ đề mới.")
            return
        await query.edit_message_text("Đang viết lại bài, chờ mình chút... ✍️")
        try:
            article = core.generate_article(topic)
        except Exception as e:
            await query.edit_message_text(f"Có lỗi khi viết bài: {e}")
            return
        context.user_data["pending_article"] = article
        context.user_data["state"] = "preview"
        await query.edit_message_text(build_preview_text(article), parse_mode="HTML", reply_markup=build_preview_keyboard())
        return

    if query.data == "confirm":
        if not article:
            await query.edit_message_text("Không tìm thấy bài viết. Hãy gửi lại chủ đề mới.")
            return
        await query.edit_message_text("Đang tải danh sách chuyên mục từ WordPress... ⏳")
        try:
            categories = core.get_wp_categories(account["wp_url"], account["wp_username"], account["wp_app_password"])
        except Exception:
            categories = []

        if not categories:
            context.user_data["category_id"] = None
            context.user_data["state"] = "awaiting_cover"
            await query.edit_message_text(
                f"Đã chốt nội dung: <b>{html.escape(article['title'])}</b>\n\n📸 Gửi cho mình ẢNH BÌA nhé.",
                parse_mode="HTML",
            )
            return

        context.user_data["categories_cache"] = categories
        buttons, row = [], []
        for cat in categories:
            row.append(InlineKeyboardButton(cat["name"], callback_data=f"cat_{cat['id']}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⏭ Bỏ qua", callback_data="cat_skip")])
        await query.edit_message_text(
            f"Đã chốt nội dung: <b>{html.escape(article['title'])}</b>\n\n📂 Chọn chuyên mục:",
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    if query.data.startswith("cat_"):
        if not article:
            await query.edit_message_text("Không tìm thấy bài viết. Hãy gửi lại chủ đề mới.")
            return
        if query.data == "cat_skip":
            context.user_data["category_id"] = None
            cat_label = "(không gắn chuyên mục)"
        else:
            cat_id = int(query.data.replace("cat_", ""))
            context.user_data["category_id"] = cat_id
            categories = context.user_data.get("categories_cache", [])
            cat_label = next((c["name"] for c in categories if c["id"] == cat_id), str(cat_id))

        context.user_data["state"] = "awaiting_cover"
        await query.edit_message_text(
            f"Đã chốt nội dung: <b>{html.escape(article['title'])}</b>\n📂 Chuyên mục: {html.escape(cat_label)}\n\n📸 Gửi ẢNH BÌA nhé.",
            parse_mode="HTML",
        )
        return


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get("state", "idle")
    article = context.user_data.get("pending_article")
    if state not in ("awaiting_cover", "awaiting_inline") or not article:
        await update.message.reply_text("Mình chưa cần ảnh lúc này. Gửi chủ đề bài viết trước nhé.")
        return

    photo = update.message.photo[-1]
    tg_file = await context.bot.get_file(photo.file_id)
    image_bytes = bytes(await tg_file.download_as_bytearray())

    if state == "awaiting_cover":
        context.user_data["cover_image"] = image_bytes
        context.user_data["state"] = "awaiting_inline"
        await update.message.reply_text("Đã nhận ảnh bìa ✅\n\n📸 Giờ gửi tiếp ẢNH CHÈN TRONG BÀI VIẾT nhé.")
        return

    if state == "awaiting_inline":
        account = db.get_or_create_account_by_telegram(update.effective_user.id)
        cover_bytes = context.user_data.get("cover_image")
        status_msg = await update.message.reply_text("Đã nhận ảnh trong bài ✅\nĐang đăng bài... 🚀")
        try:
            result = core.publish_article(account, article, context.user_data.get("category_id"), cover_bytes, image_bytes)

            sheet_line = "📊 (Chưa kết nối Google Sheet)\n"
            if not result["sheet_skipped"]:
                sheet_line = (
                    f"📊 Đã ghi vào Google Sheet.\n📄 {result['sheet_url']}\n"
                    if result["sheet_ok"] else f"⚠️ Ghi Google Sheet thất bại: {result['sheet_error']}\n"
                )

            fb_line = "📘 (Chưa kết nối Facebook Page)\n"
            if not result["fb_skipped"]:
                if result["fb_ok"]:
                    fb_line = "📘 Đã đăng lên Facebook Page.\n"
                    if result["fb_link"]:
                        fb_line += f"🔗 {result['fb_link']}\n"
                else:
                    fb_line = f"⚠️ Đăng Facebook Page thất bại: {result['fb_error']}\n"

            await status_msg.edit_text(
                f"✅ Đã đăng bài thành công!\n\nTiêu đề: {article['title']}\n"
                f"Trạng thái: {result['status']}\nLink: {result['link']}\n\n{sheet_line}{fb_line}"
            )

            share_url = "https://www.facebook.com/sharer/sharer.php?" + urlencode({"u": result["link"]})
            share_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("📤 Mở Facebook để chia sẻ", url=share_url)]])
            await update.message.reply_text(
                f"📋 Nội dung soạn sẵn cho Group Facebook:\n――――――――――――――\n{result['share_text']}\n――――――――――――――",
                reply_markup=share_keyboard,
            )
        except Exception as e:
            logger.exception("Lỗi khi đăng bài")
            await status_msg.edit_text(f"Có lỗi xảy ra: {e}")
        finally:
            reset_state(context)


def build_bot_application():
    if not TELEGRAM_BOT_TOKEN:
        return None
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("link_web", link_web_command))
    app.add_handler(CommandHandler("upgrade", upgrade_command))
    app.add_handler(CommandHandler("connect_wp", connect_wp_command))
    app.add_handler(CommandHandler("connect_sheet", connect_sheet_command))
    app.add_handler(CommandHandler("connect_facebook", connect_facebook_command))
    app.add_handler(CommandHandler("set_cta", set_cta_command))
    app.add_handler(CommandHandler("set_status", set_status_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("sheet", sheet_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_topic))
    app.add_handler(CallbackQueryHandler(handle_button))
    return app


def run_bot_blocking():
    """Chạy bot polling - hàm này BLOCKING, gọi trong 1 thread riêng."""
    app = build_bot_application()
    if not app:
        logger.warning("TELEGRAM_BOT_TOKEN chưa cấu hình - bot Telegram sẽ không chạy.")
        return

    # Python 3.10+ không còn tự tạo event loop mặc định cho thread phụ (không phải main thread) -
    # phải tự tạo và gán 1 event loop riêng cho thread nền này trước khi gọi run_polling().
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    logger.info("Bot Telegram đang chạy...")
    app.run_polling(close_loop=False, stop_signals=None)
