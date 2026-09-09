"""
db.py - Lớp truy cập dữ liệu dùng CHUNG cho cả Web App và Telegram Bot.
Cả 2 hệ thống đọc/ghi vào cùng 1 file SQLite, cùng 1 bảng "accounts" -
đây chính là cách "dùng chung 1 hệ thống" giữa web và Telegram.

Một account có thể được định danh bởi:
  - email + password_hash  (đăng nhập qua Web)
  - telegram_user_id        (dùng qua Telegram, tự động tạo khi /start lần đầu)
Một người có thể có cả 2 (nếu họ tự đăng nhập web bằng đúng email đã liên kết),
nhưng mặc định 2 lối vào là độc lập với nhau trừ khi được liên kết thủ công.

Mỗi account có 24h dùng thử miễn phí kể từ lúc tạo (trial_active_until),
sau đó cần mua gói (bảng "orders") để tiếp tục dùng tính năng viết bài AI.
"""

import os
import secrets
import sqlite3
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash, check_password_hash

DB_PATH = os.getenv("DB_PATH", "app_data.db")
TRIAL_HOURS = 24


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(conn, table, column, coltype):
    existing = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            password_hash TEXT,
            telegram_user_id INTEGER UNIQUE,
            display_name TEXT,
            gemini_api_key TEXT,
            wp_url TEXT,
            wp_username TEXT,
            wp_app_password TEXT,
            wp_status TEXT DEFAULT 'draft',
            sheet_id TEXT,
            fb_page_id TEXT,
            fb_page_token TEXT,
            cta_text TEXT,
            cta_url TEXT,
            created_at TEXT
        )
        """
    )
    # Migrate: thêm cột mới cho tài khoản/gói dùng (an toàn với database đã tồn tại từ trước)
    _ensure_column(conn, "accounts", "is_admin", "INTEGER DEFAULT 0")
    _ensure_column(conn, "accounts", "subscription_active_until", "TEXT")
    _ensure_column(conn, "accounts", "trial_active_until", "TEXT")
    _ensure_column(conn, "accounts", "posts_published", "INTEGER DEFAULT 0")
    _ensure_column(conn, "accounts", "posts_attempts", "INTEGER DEFAULT 0")
    _ensure_column(conn, "accounts", "current_plan_code", "TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_code TEXT UNIQUE,
            account_id INTEGER,
            plan_code TEXT,
            months INTEGER,
            amount INTEGER,
            is_first_order INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            note TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )

    # Lịch sử từng bài đăng - dùng cho "Bài viết gần đây" trên Dashboard
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            title TEXT,
            link TEXT,
            category TEXT,
            status TEXT,
            created_at TEXT
        )
        """
    )

    # Nhiều website/tài khoản - mỗi website có bộ WordPress + Facebook + Sheet + CTA riêng độc lập.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS websites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_id INTEGER,
            label TEXT,
            wp_url TEXT,
            wp_username TEXT,
            wp_app_password TEXT,
            wp_status TEXT DEFAULT 'draft',
            fb_page_id TEXT,
            fb_page_token TEXT,
            sheet_id TEXT,
            cta_text TEXT,
            cta_url TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()

    # Migration 1 lần: tài khoản cũ đã có wp_url ở bảng accounts (trước khi có bảng websites)
    # nhưng chưa có dòng nào trong "websites" -> tự tạo "Website 1" từ dữ liệu cũ, không mất gì.
    old_accounts = conn.execute(
        "SELECT * FROM accounts WHERE wp_url IS NOT NULL AND wp_url != ''"
    ).fetchall()
    for acc in old_accounts:
        has_website = conn.execute(
            "SELECT 1 FROM websites WHERE account_id = ? LIMIT 1", (acc["id"],)
        ).fetchone()
        if has_website:
            continue
        conn.execute(
            "INSERT INTO websites (account_id, label, wp_url, wp_username, wp_app_password, wp_status, "
            "fb_page_id, fb_page_token, sheet_id, cta_text, cta_url, created_at) "
            "VALUES (?, 'Website 1', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                acc["id"], acc["wp_url"], acc["wp_username"], acc["wp_app_password"],
                acc["wp_status"] or "draft", acc["fb_page_id"], acc["fb_page_token"],
                acc["sheet_id"], acc["cta_text"], acc["cta_url"], datetime.now().isoformat(),
            ),
        )
    conn.commit()
    conn.close()


def _row_to_dict(row):
    return dict(row) if row else None


def _new_trial_deadline() -> str:
    return (datetime.now() + timedelta(hours=TRIAL_HOURS)).isoformat()


def get_account(account_id: int) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
    conn.close()
    return _row_to_dict(row) or {}


def get_account_by_email(email: str) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE email = ?", (email.lower().strip(),)).fetchone()
    conn.close()
    return _row_to_dict(row)


def get_account_by_telegram_id(telegram_user_id: int) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM accounts WHERE telegram_user_id = ?", (telegram_user_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def create_account_with_email(email: str, password: str, display_name: str = "") -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO accounts (email, password_hash, display_name, wp_status, trial_active_until, created_at) "
        "VALUES (?, ?, ?, 'draft', ?, ?)",
        (email.lower().strip(), generate_password_hash(password), display_name,
         _new_trial_deadline(), datetime.now().isoformat()),
    )
    conn.commit()
    account_id = cur.lastrowid
    conn.close()
    return account_id


def verify_password(email: str, password: str) -> dict:
    """Trả về account nếu đúng mật khẩu, None nếu sai."""
    account = get_account_by_email(email)
    if not account or not account.get("password_hash"):
        return None
    if check_password_hash(account["password_hash"], password):
        return account
    return None


def get_or_create_account_by_telegram(telegram_user_id: int) -> dict:
    account = get_account_by_telegram_id(telegram_user_id)
    if account:
        return account
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO accounts (telegram_user_id, wp_status, trial_active_until, created_at) VALUES (?, 'draft', ?, ?)",
        (telegram_user_id, _new_trial_deadline(), datetime.now().isoformat()),
    )
    conn.commit()
    account_id = cur.lastrowid
    conn.close()
    return get_account(account_id)


def update_account(account_id: int, **fields):
    if not fields:
        return
    conn = get_db()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE accounts SET {set_clause} WHERE id = ?", (*fields.values(), account_id))
    conn.commit()
    conn.close()


# ---------- Truy cập / Gói dùng ----------

def account_access_status(account: dict) -> dict:
    """
    Trả về {active: bool, source: 'trial'|'subscription'|None, expires_at: datetime|None}
    """
    now = datetime.now()
    sub_until = account.get("subscription_active_until")
    if sub_until:
        try:
            sub_dt = datetime.fromisoformat(sub_until)
            if sub_dt > now:
                return {"active": True, "source": "subscription", "expires_at": sub_dt}
        except ValueError:
            pass

    trial_until = account.get("trial_active_until")
    if trial_until:
        try:
            trial_dt = datetime.fromisoformat(trial_until)
            if trial_dt > now:
                return {"active": True, "source": "trial", "expires_at": trial_dt}
        except ValueError:
            pass

    # Đã hết hạn - vẫn báo về thời điểm hết hạn gần nhất (nếu có) để hiển thị
    latest = None
    for raw in (sub_until, trial_until):
        if raw:
            try:
                d = datetime.fromisoformat(raw)
                if not latest or d > latest:
                    latest = d
            except ValueError:
                pass
    return {"active": False, "source": None, "expires_at": latest}


def extend_subscription(account_id: int, months: int, plan_code: str = None):
    account = get_account(account_id)
    now = datetime.now()
    current = None
    if account.get("subscription_active_until"):
        try:
            current = datetime.fromisoformat(account["subscription_active_until"])
        except ValueError:
            current = None
    base = current if (current and current > now) else now
    new_until = base + timedelta(days=31 * months)
    fields = {"subscription_active_until": new_until.isoformat()}
    if plan_code:
        # Gói mới mua luôn ghi đè lên gói cũ đang lưu (mua gói cao hơn/thấp hơn đều cập nhật lại
        # đúng số lượt viết bài đồng thời theo gói MỚI NHẤT vừa duyệt).
        fields["current_plan_code"] = plan_code
    update_account(account_id, **fields)
    return new_until


def max_write_slots(account: dict, plans: dict, trial_slots: int = 1) -> int:
    """Số bài được phép soạn ĐỒNG THỜI, dựa theo gói tài khoản đang dùng.
    Tài khoản chưa mua gói nào (đang dùng thử) mặc định 1 lượt."""
    access = account_access_status(account)
    if not access["active"]:
        return 0
    if access["source"] == "trial":
        return trial_slots
    plan = plans.get(account.get("current_plan_code"))
    return plan["slots"] if plan else trial_slots


# ---------- Orders (đơn hàng mua gói) ----------

def create_order(account_id: int, plan_code: str, months: int, amount: int, is_first_order: bool) -> dict:
    order_code = "DH" + secrets.token_hex(3).upper()
    conn = get_db()
    now = datetime.now().isoformat()
    cur = conn.execute(
        "INSERT INTO orders (order_code, account_id, plan_code, months, amount, is_first_order, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (order_code, account_id, plan_code, months, amount, int(is_first_order), now, now),
    )
    conn.commit()
    order_id = cur.lastrowid
    conn.close()
    return get_order(order_id)


def get_order(order_id: int) -> dict:
    conn = get_db()
    row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    conn.close()
    return _row_to_dict(row)


def has_any_paid_order(account_id: int) -> bool:
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM orders WHERE account_id = ? AND status = 'paid' LIMIT 1", (account_id,)
    ).fetchone()
    conn.close()
    return row is not None


def list_orders_for_account(account_id: int) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM orders WHERE account_id = ? ORDER BY created_at DESC", (account_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_orders(status: str = None) -> list:
    conn = get_db()
    if status:
        rows = conn.execute(
            "SELECT orders.*, accounts.email, accounts.display_name FROM orders "
            "JOIN accounts ON accounts.id = orders.account_id "
            "WHERE orders.status = ? ORDER BY orders.created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT orders.*, accounts.email, accounts.display_name FROM orders "
            "JOIN accounts ON accounts.id = orders.account_id "
            "ORDER BY orders.created_at DESC"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_order_status(order_id: int, status: str):
    conn = get_db()
    conn.execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
        (status, datetime.now().isoformat(), order_id),
    )
    conn.commit()
    conn.close()


# ---------- Thống kê (hiện trên Dashboard) ----------

def record_publish_attempt(account_id: int, success: bool):
    """Gọi mỗi khi có 1 lượt đăng bài (thành công hoặc lỗi) để tính số liệu Dashboard thật."""
    conn = get_db()
    if success:
        conn.execute(
            "UPDATE accounts SET posts_attempts = COALESCE(posts_attempts, 0) + 1, "
            "posts_published = COALESCE(posts_published, 0) + 1 WHERE id = ?",
            (account_id,),
        )
    else:
        conn.execute(
            "UPDATE accounts SET posts_attempts = COALESCE(posts_attempts, 0) + 1 WHERE id = ?",
            (account_id,),
        )
    conn.commit()
    conn.close()


def record_post(account_id: int, title: str, link: str, category: str, status: str):
    """Lưu lại chi tiết 1 bài vừa đăng thành công - dùng cho danh sách 'Bài viết gần đây'."""
    conn = get_db()
    conn.execute(
        "INSERT INTO posts (account_id, title, link, category, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (account_id, title, link, category, status, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def list_recent_posts(account_id: int, limit: int = 5) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM posts WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_posts(account_id: int) -> int:
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) AS c FROM posts WHERE account_id = ?", (account_id,)).fetchone()
    conn.close()
    return row["c"] if row else 0


def list_posts_paginated(account_id: int, page: int = 1, per_page: int = 15) -> list:
    page = max(1, page)
    offset = (page - 1) * per_page
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM posts WHERE account_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (account_id, per_page, offset),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def category_breakdown(account_id: int, limit: int = 5) -> list:
    """Thống kê số bài đã đăng theo từng chuyên mục - dùng cho khối 'Danh mục phổ biến'."""
    conn = get_db()
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(category, ''), 'Chưa phân loại') AS cat, COUNT(*) AS cnt "
        "FROM posts WHERE account_id = ? GROUP BY cat ORDER BY cnt DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()
    conn.close()
    total = sum(r["cnt"] for r in rows) or 1
    return [{"name": r["cat"], "count": r["cnt"], "pct": round(r["cnt"] / total * 100)} for r in rows]


def list_recent_activity(account_id: int, limit: int = 6) -> list:
    """Ghép lịch sử bài đăng + đơn hàng thành 1 dòng thời gian hoạt động - dùng cho Dashboard."""
    conn = get_db()
    post_rows = conn.execute(
        "SELECT title, created_at FROM posts WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()
    order_rows = conn.execute(
        "SELECT plan_code, months, status, created_at FROM orders WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()
    conn.close()

    items = []
    for r in post_rows:
        items.append({"icon": "✎", "text": f"Đã đăng bài: {r['title']}", "created_at": r["created_at"]})

    order_labels = {"pending": "Đã tạo đơn hàng", "paid": "Đã thanh toán đơn hàng", "rejected": "Đơn hàng bị từ chối"}
    for r in order_rows:
        label = order_labels.get(r["status"], "Cập nhật đơn hàng")
        items.append({"icon": "💳", "text": f"{label} - gói {r['months']} tháng", "created_at": r["created_at"]})

    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]


# ---------- Website (nhiều website/tài khoản, mỗi cái có WP + FB + Sheet + CTA riêng) ----------

def list_websites(account_id: int) -> list:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM websites WHERE account_id = ? ORDER BY id ASC", (account_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_website(website_id: int, account_id: int) -> dict:
    """Luôn kèm account_id để đảm bảo không sửa/xem nhầm website của tài khoản khác."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM websites WHERE id = ? AND account_id = ?", (website_id, account_id)
    ).fetchone()
    conn.close()
    return _row_to_dict(row)


def count_websites(account_id: int) -> int:
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) AS c FROM websites WHERE account_id = ?", (account_id,)).fetchone()
    conn.close()
    return row["c"] if row else 0


def create_website(account_id: int, label: str = None) -> int:
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO websites (account_id, label, wp_status, created_at) VALUES (?, ?, 'draft', ?)",
        (account_id, label or None, datetime.now().isoformat()),
    )
    conn.commit()
    website_id = cur.lastrowid
    conn.close()
    return website_id


def update_website(website_id: int, account_id: int, **fields):
    if not fields:
        return
    conn = get_db()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE websites SET {set_clause} WHERE id = ? AND account_id = ?",
        (*fields.values(), website_id, account_id),
    )
    conn.commit()
    conn.close()


def delete_website(website_id: int, account_id: int):
    conn = get_db()
    conn.execute("DELETE FROM websites WHERE id = ? AND account_id = ?", (website_id, account_id))
    conn.commit()
    conn.close()


def max_websites(account: dict, plans: dict, trial_max: int = 1):
    """Số website tối đa được kết nối - None nghĩa là KHÔNG GIỚI HẠN."""
    access = account_access_status(account)
    if not access["active"]:
        return 0
    if access["source"] == "trial":
        return trial_max
    plan = plans.get(account.get("current_plan_code"))
    if not plan:
        return trial_max
    return plan.get("max_sites", trial_max)

