from pathlib import Path
import re
import shutil
import datetime

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
STATIC_UPLOAD_DIR = STATIC_DIR / "uploads" / "promos"

APP_PATH = BASE_DIR / "app.py"
INDEX_PATH = TEMPLATES_DIR / "index.html"
ADMIN_PATH = TEMPLATES_DIR / "admin.html"
PROMO_REQUEST_PATH = TEMPLATES_DIR / "promo_request.html"
PROMOTION_DETAIL_PATH = TEMPLATES_DIR / "promotion_detail.html"
PROMO_JS_PATH = STATIC_DIR / "promo_popup.js"
PROMO_CSS_PATH = STATIC_DIR / "promo_popup.css"


ADMIN_EXTENSION = r'''
# ==============================
# Admin Management + Promotion Extension
# 홍보 신청 / 홍보 이미지 / 관리자 승인 / 메인 팝업 / 계정 제재 / 실시간 통계
# ==============================

PROMO_UPLOAD_DIR = os.path.join("static", "uploads", "promos")
PROMO_MAX_IMAGE_BYTES = 5 * 1024 * 1024
PROMO_ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def init_admin_management_db():
    conn = get_db()
    cur = conn.cursor()

    if not column_exists(conn, "users", "account_status"):
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN account_status TEXT NOT NULL DEFAULT 'active'
        """)

    if not column_exists(conn, "users", "status_reason"):
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN status_reason TEXT
        """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS promo_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            requester_user_id INTEGER NOT NULL,
            requester_role TEXT NOT NULL,
            requester_name TEXT NOT NULL,

            title TEXT NOT NULL,
            category TEXT NOT NULL,
            target_url TEXT,
            content TEXT NOT NULL,

            promo_what TEXT,
            promo_reason TEXT,
            image_path TEXT,
            image_original_name TEXT,
            image_size_bytes INTEGER,

            status TEXT NOT NULL DEFAULT 'pending',
            admin_note TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT,
            reviewed_by TEXT
        )
    """)

    for column_sql in [
        ("promo_what", "ALTER TABLE promo_requests ADD COLUMN promo_what TEXT"),
        ("promo_reason", "ALTER TABLE promo_requests ADD COLUMN promo_reason TEXT"),
        ("image_path", "ALTER TABLE promo_requests ADD COLUMN image_path TEXT"),
        ("image_original_name", "ALTER TABLE promo_requests ADD COLUMN image_original_name TEXT"),
        ("image_size_bytes", "ALTER TABLE promo_requests ADD COLUMN image_size_bytes INTEGER"),
    ]:
        column_name, sql = column_sql
        if not column_exists(conn, "promo_requests", column_name):
            cur.execute(sql)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_role TEXT,
            event_type TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            detail TEXT,
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

    os.makedirs(PROMO_UPLOAD_DIR, exist_ok=True)


init_admin_management_db()


def allowed_promo_image(filename):
    if not filename or "." not in filename:
        return False

    ext = filename.rsplit(".", 1)[1].lower()
    return ext in PROMO_ALLOWED_EXTENSIONS


def save_promo_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None, None, None, "홍보물 이미지를 업로드해주세요."

    original_name = file_storage.filename

    if not allowed_promo_image(original_name):
        return None, None, None, "홍보물 이미지는 png, jpg, jpeg, webp, gif 파일만 업로드할 수 있습니다."

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)

    if size <= 0:
        return None, None, None, "비어 있는 이미지 파일은 업로드할 수 없습니다."

    if size > PROMO_MAX_IMAGE_BYTES:
        return None, None, None, "홍보물 이미지는 5MB 이하로 업로드해주세요."

    ext = original_name.rsplit(".", 1)[1].lower()
    saved_name = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(PROMO_UPLOAD_DIR, saved_name)

    file_storage.save(save_path)

    web_path = f"/static/uploads/promos/{saved_name}"
    return web_path, original_name, size, None


def safe_json_dumps(data):
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return "{}"


def log_usage_event(event_type, tool_name, detail=None):
    try:
        if request.path.startswith("/api/admin"):
            return

        if request.path.startswith("/static"):
            return

        user_id = session.get("user_id")
        user_role = session.get("user_role")

        if session.get("is_admin") is True:
            user_id = 0
            user_role = "admin"

        conn = get_db()
        conn.execute("""
            INSERT INTO usage_events (
                user_id, user_role, event_type, tool_name, detail, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            user_role or "guest",
            event_type,
            tool_name,
            safe_json_dumps(detail or {}),
            datetime.datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()

    except Exception:
        pass


@app.before_request
def blocked_account_guard():
    if request.path.startswith("/static"):
        return None

    if request.path in ["/logout", "/login"]:
        return None

    if session.get("is_admin") is True:
        return None

    user_id = session.get("user_id")
    if not user_id:
        return None

    try:
        conn = get_db()
        user = conn.execute("""
            SELECT id, account_status, status_reason
            FROM users
            WHERE id = ?
        """, (user_id,)).fetchone()
        conn.close()

        if not user:
            session.clear()
            if request.path.startswith("/api/"):
                return jsonify({
                    "success": False,
                    "message": "존재하지 않는 계정입니다."
                }), 403

            flash("계정 정보를 찾을 수 없어 로그아웃되었습니다.")
            return redirect(url_for("login_page"))

        if user["account_status"] != "active":
            reason = user["status_reason"] or "관리자에 의해 계정 이용이 제한되었습니다."
            session.clear()

            if request.path.startswith("/api/"):
                return jsonify({
                    "success": False,
                    "message": reason
                }), 403

            flash(reason)
            return redirect(url_for("login_page"))

    except Exception:
        return None

    return None


@app.after_request
def inject_promo_popup_assets(response):
    try:
        if request.path != "/":
            return response

        content_type = response.headers.get("Content-Type", "")

        if "text/html" not in content_type:
            return response

        html = response.get_data(as_text=True)

        css_tag = '<link rel="stylesheet" href="/static/promo_popup.css">'
        js_tag = '<script src="/static/promo_popup.js"></script>'

        if css_tag not in html and "</head>" in html.lower():
            html = re.sub(
                r"</head\s*>",
                css_tag + "\n</head>",
                html,
                count=1,
                flags=re.IGNORECASE
            )

        if js_tag not in html and "</body>" in html.lower():
            html = re.sub(
                r"</body\s*>",
                js_tag + "\n</body>",
                html,
                count=1,
                flags=re.IGNORECASE
            )

        response.set_data(html)
        response.headers["Content-Length"] = str(len(response.get_data()))

    except Exception:
        return response

    return response


@app.after_request
def usage_logger_after_request(response):
    try:
        if response.status_code >= 500:
            log_usage_event("error", "서버 오류", {
                "path": request.path,
                "status_code": response.status_code
            })
            return response

        if response.status_code >= 400:
            return response

        if request.path == "/api/search":
            query = request.args.get("query", "")
            if query:
                log_usage_event("search", "검색", {"query": query})

        elif request.path == "/api/chat" and request.method == "POST":
            data = request.get_json(silent=True) or {}
            message = data.get("message", "")
            log_usage_event("chat", "챗봇", {"message_length": len(message)})

        elif request.path == "/api/meal":
            log_usage_event("tool", "급식", {"mode": request.args.get("mode", "today")})

        elif request.path == "/api/timetable":
            log_usage_event("tool", "시간표", {
                "grade": request.args.get("grade", ""),
                "class": request.args.get("class", "")
            })

        elif request.path == "/api/game/start":
            data = request.get_json(silent=True) or {}
            log_usage_event("game", "미니게임 시작", {"game_type": data.get("gameType", "")})

        elif request.path == "/api/game/submit":
            data = request.get_json(silent=True) or {}
            log_usage_event("game", "미니게임 점수 제출", {
                "game_type": data.get("gameType", ""),
                "score": data.get("score", 0)
            })

        elif request.path == "/teachers":
            log_usage_event("page", "선생님 목록", {})

        elif request.path == "/game":
            log_usage_event("page", "미니게임 페이지", {})

        elif request.path == "/profile":
            log_usage_event("page", "프로필", {})

        elif request.path == "/promo/request" and request.method == "POST":
            log_usage_event("promo", "홍보 신청", {})

        elif request.path.startswith("/promotion/"):
            log_usage_event("promo", "홍보 상세", {"path": request.path})

    except Exception:
        pass

    return response


@app.route("/promo/request", methods=["GET", "POST"])
@login_required
def promo_request_page():
    current_user = get_current_user()

    if current_user["role"] == "admin":
        flash("관리자는 홍보 신청을 제출할 수 없습니다.")
        return redirect(url_for("admin_page"))

    if request.method == "POST":
        promo_what = request.form.get("promoWhat", "").strip()
        promo_reason = request.form.get("promoReason", "").strip()
        promo_image = request.files.get("promoImage")

        if not promo_what or not promo_reason:
            flash("홍보할 내용과 사유를 모두 입력해주세요.")
            return render_template("promo_request.html")

        if len(promo_what) > 80:
            flash("홍보할 내용은 80자 이내로 입력해주세요.")
            return render_template("promo_request.html")

        if len(promo_reason) > 1000:
            flash("사유는 1000자 이내로 입력해주세요.")
            return render_template("promo_request.html")

        image_path, original_name, image_size, image_error = save_promo_image(promo_image)

        if image_error:
            flash(image_error)
            return render_template("promo_request.html")

        conn = get_db()
        conn.execute("""
            INSERT INTO promo_requests (
                requester_user_id,
                requester_role,
                requester_name,

                title,
                category,
                target_url,
                content,

                promo_what,
                promo_reason,
                image_path,
                image_original_name,
                image_size_bytes,

                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            current_user["id"],
            current_user["role"],
            current_user["student_name"],

            promo_what,
            "홍보",
            None,
            promo_reason,

            promo_what,
            promo_reason,
            image_path,
            original_name,
            image_size,

            "pending",
            datetime.datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()

        flash("홍보 신청이 관리자에게 제출되었습니다.")
        return redirect(url_for("promo_request_page"))

    return render_template("promo_request.html")


@app.route("/api/promos/approved")
def approved_promos_api():
    conn = get_db()
    rows = conn.execute("""
        SELECT
            id,
            requester_role,
            requester_name,
            COALESCE(promo_what, title) AS promo_what,
            COALESCE(promo_reason, content) AS promo_reason,
            image_path,
            created_at
        FROM promo_requests
        WHERE status = 'approved'
        AND image_path IS NOT NULL
        ORDER BY id DESC
        LIMIT 30
    """).fetchall()
    conn.close()

    promos = []
    for row in rows:
        promos.append({
            "id": row["id"],
            "requester_role": row["requester_role"],
            "requester_name": row["requester_name"],
            "promo_what": row["promo_what"],
            "promo_reason": row["promo_reason"],
            "image_path": row["image_path"],
            "created_at": row["created_at"]
        })

    return jsonify({
        "success": True,
        "promos": promos
    })


@app.route("/promotion/<int:promo_id>")
def promotion_detail_page(promo_id):
    current_user = get_current_user()

    conn = get_db()
    promo = conn.execute("""
        SELECT
            id,
            requester_role,
            requester_name,
            COALESCE(promo_what, title) AS promo_what,
            COALESCE(promo_reason, content) AS promo_reason,
            image_path,
            image_original_name,
            image_size_bytes,
            status,
            admin_note,
            created_at,
            reviewed_at
        FROM promo_requests
        WHERE id = ?
    """, (promo_id,)).fetchone()
    conn.close()

    if not promo:
        flash("존재하지 않는 홍보물입니다.")
        return redirect(url_for("home"))

    is_admin = current_user and current_user["role"] == "admin"

    if promo["status"] != "approved" and not is_admin:
        flash("아직 공개되지 않은 홍보물입니다.")
        return redirect(url_for("home"))

    return render_template("promotion_detail.html", promo=promo)


def row_to_dict(row):
    if not row:
        return None
    return {key: row[key] for key in row.keys()}


def fetch_admin_dashboard_data():
    conn = get_db()

    today_prefix = datetime.date.today().isoformat()
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()

    total_users = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE account_status != 'deleted'
    """).fetchone()["cnt"]

    student_count = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE role = 'student'
        AND account_status != 'deleted'
    """).fetchone()["cnt"]

    teacher_count = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE role = 'teacher'
        AND account_status != 'deleted'
    """).fetchone()["cnt"]

    active_count = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE account_status = 'active'
    """).fetchone()["cnt"]

    restricted_count = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE account_status = 'restricted'
    """).fetchone()["cnt"]

    deleted_count = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE account_status = 'deleted'
    """).fetchone()["cnt"]

    pending_promos = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM promo_requests
        WHERE status = 'pending'
    """).fetchone()["cnt"]

    approved_promos = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM promo_requests
        WHERE status = 'approved'
    """).fetchone()["cnt"]

    today_total_usage = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM usage_events
        WHERE created_at LIKE ?
    """, (today_prefix + "%",)).fetchone()["cnt"]

    today_search = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM usage_events
        WHERE event_type = 'search'
        AND created_at LIKE ?
    """, (today_prefix + "%",)).fetchone()["cnt"]

    today_chat = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM usage_events
        WHERE event_type = 'chat'
        AND created_at LIKE ?
    """, (today_prefix + "%",)).fetchone()["cnt"]

    today_game = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM usage_events
        WHERE event_type = 'game'
        AND created_at LIKE ?
    """, (today_prefix + "%",)).fetchone()["cnt"]

    top_tools_rows = conn.execute("""
        SELECT tool_name, COUNT(*) AS count
        FROM usage_events
        WHERE created_at >= ?
        GROUP BY tool_name
        ORDER BY count DESC
        LIMIT 8
    """, (week_ago,)).fetchall()

    daily_rows = conn.execute("""
        SELECT substr(created_at, 1, 10) AS day, COUNT(*) AS count
        FROM usage_events
        WHERE created_at >= ?
        GROUP BY substr(created_at, 1, 10)
        ORDER BY day ASC
    """, (week_ago,)).fetchall()

    recent_users_rows = conn.execute("""
        SELECT
            id,
            username,
            student_id,
            student_name,
            role,
            account_status,
            status_reason,
            created_at
        FROM users
        ORDER BY id DESC
        LIMIT 80
    """).fetchall()

    promo_rows = conn.execute("""
        SELECT
            id,
            requester_user_id,
            requester_role,
            requester_name,
            COALESCE(promo_what, title) AS promo_what,
            COALESCE(promo_reason, content) AS promo_reason,
            image_path,
            image_original_name,
            image_size_bytes,
            status,
            admin_note,
            created_at,
            reviewed_at,
            reviewed_by
        FROM promo_requests
        ORDER BY
            CASE status
                WHEN 'pending' THEN 0
                WHEN 'approved' THEN 1
                WHEN 'rejected' THEN 2
                ELSE 3
            END,
            id DESC
        LIMIT 80
    """).fetchall()

    recent_events_rows = conn.execute("""
        SELECT
            id,
            user_id,
            user_role,
            event_type,
            tool_name,
            detail,
            created_at
        FROM usage_events
        ORDER BY id DESC
        LIMIT 40
    """).fetchall()

    game_count = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM game_scores
    """).fetchone()["cnt"]

    conn.close()

    important_cards = []

    if pending_promos > 0:
        important_cards.append({
            "level": "warning",
            "title": "승인 대기 홍보 신청",
            "message": f"{pending_promos}개의 홍보 신청이 승인 대기 중입니다."
        })

    if restricted_count > 0:
        important_cards.append({
            "level": "danger",
            "title": "제재 계정 존재",
            "message": f"{restricted_count}개의 계정이 제재 상태입니다."
        })

    if today_chat >= 20:
        important_cards.append({
            "level": "info",
            "title": "챗봇 사용량 증가",
            "message": f"오늘 챗봇 요청이 {today_chat}회 발생했습니다. API 사용량을 확인하세요."
        })

    if today_total_usage == 0:
        important_cards.append({
            "level": "muted",
            "title": "오늘 사용 기록 없음",
            "message": "아직 오늘 기록된 사용 로그가 없습니다."
        })

    if not important_cards:
        important_cards.append({
            "level": "good",
            "title": "운영 상태 정상",
            "message": "현재 승인 대기, 제재 위험, 과도한 사용량이 발견되지 않았습니다."
        })

    return {
        "summary": {
            "total_users": total_users,
            "student_count": student_count,
            "teacher_count": teacher_count,
            "active_count": active_count,
            "restricted_count": restricted_count,
            "deleted_count": deleted_count,
            "pending_promos": pending_promos,
            "approved_promos": approved_promos,
            "today_total_usage": today_total_usage,
            "today_search": today_search,
            "today_chat": today_chat,
            "today_game": today_game,
            "game_count": game_count,
        },
        "important_cards": important_cards,
        "top_tools": [row_to_dict(row) for row in top_tools_rows],
        "daily_usage": [row_to_dict(row) for row in daily_rows],
        "users": [row_to_dict(row) for row in recent_users_rows],
        "promo_requests": [row_to_dict(row) for row in promo_rows],
        "recent_events": [row_to_dict(row) for row in recent_events_rows],
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


@app.route("/api/admin/dashboard")
@admin_required
def admin_dashboard_api():
    return jsonify({
        "success": True,
        "data": fetch_admin_dashboard_data()
    })


@app.route("/api/admin/user-action", methods=["POST"])
@admin_required
def admin_user_action_api():
    data = request.get_json(silent=True) or {}

    action = data.get("action", "").strip()
    target_user_id = data.get("targetUserId")
    reason = data.get("reason", "").strip()

    if action not in ["restrict", "unrestrict", "delete"]:
        return jsonify({
            "success": False,
            "message": "올바르지 않은 작업입니다."
        }), 400

    try:
        target_user_id = int(target_user_id)
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "대상 계정 정보가 올바르지 않습니다."
        }), 400

    conn = get_db()

    target = conn.execute("""
        SELECT id, username, student_name, role, account_status
        FROM users
        WHERE id = ?
    """, (target_user_id,)).fetchone()

    if not target:
        conn.close()
        return jsonify({
            "success": False,
            "message": "대상 계정을 찾을 수 없습니다."
        }), 404

    if action == "restrict":
        if not reason:
            reason = "관리자에 의해 계정 이용이 제한되었습니다."

        conn.execute("""
            UPDATE users
            SET account_status = 'restricted',
                status_reason = ?
            WHERE id = ?
        """, (reason, target_user_id))

        message = f"{target['student_name']} 계정을 제재했습니다."

    elif action == "unrestrict":
        conn.execute("""
            UPDATE users
            SET account_status = 'active',
                status_reason = NULL
            WHERE id = ?
        """, (target_user_id,))

        message = f"{target['student_name']} 계정 제재를 해제했습니다."

    else:
        if not reason:
            reason = "관리자에 의해 계정이 삭제 처리되었습니다."

        conn.execute("""
            UPDATE users
            SET account_status = 'deleted',
                status_reason = ?
            WHERE id = ?
        """, (reason, target_user_id))

        message = f"{target['student_name']} 계정을 삭제 처리했습니다."

    conn.commit()
    conn.close()

    log_usage_event("admin", "관리자 계정 관리", {
        "action": action,
        "target_user_id": target_user_id
    })

    return jsonify({
        "success": True,
        "message": message,
        "data": fetch_admin_dashboard_data()
    })


@app.route("/api/admin/promo-action", methods=["POST"])
@admin_required
def admin_promo_action_api():
    data = request.get_json(silent=True) or {}

    action = data.get("action", "").strip()
    promo_request_id = data.get("promoRequestId")
    admin_note = data.get("adminNote", "").strip()

    if action not in ["approve", "reject"]:
        return jsonify({
            "success": False,
            "message": "올바르지 않은 작업입니다."
        }), 400

    try:
        promo_request_id = int(promo_request_id)
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "홍보 신청 정보가 올바르지 않습니다."
        }), 400

    status = "approved" if action == "approve" else "rejected"

    conn = get_db()

    target = conn.execute("""
        SELECT
            id,
            COALESCE(promo_what, title) AS promo_what,
            image_path,
            status
        FROM promo_requests
        WHERE id = ?
    """, (promo_request_id,)).fetchone()

    if not target:
        conn.close()
        return jsonify({
            "success": False,
            "message": "홍보 신청을 찾을 수 없습니다."
        }), 404

    if status == "approved" and not target["image_path"]:
        conn.close()
        return jsonify({
            "success": False,
            "message": "홍보물 이미지가 없는 신청은 승인할 수 없습니다."
        }), 400

    conn.execute("""
        UPDATE promo_requests
        SET status = ?,
            admin_note = ?,
            reviewed_at = ?,
            reviewed_by = ?
        WHERE id = ?
    """, (
        status,
        admin_note,
        datetime.datetime.now().isoformat(),
        ADMIN_USERNAME,
        promo_request_id
    ))

    conn.commit()
    conn.close()

    log_usage_event("admin", "관리자 홍보 심사", {
        "action": action,
        "promo_request_id": promo_request_id
    })

    if status == "approved":
        message = "홍보 신청을 승인했습니다. 승인된 홍보물 이미지는 메인 팝업과 상세 페이지에 표시됩니다."
    else:
        message = "홍보 신청을 거절했습니다."

    return jsonify({
        "success": True,
        "message": message,
        "data": fetch_admin_dashboard_data()
    })
'''


ADMIN_HTML = r'''<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>광글 관리자</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
  <style>
    body { background:#fff; color:#202124; }
    .admin-main { max-width:1280px; margin:0 auto; padding:28px 18px 48px; }
    .admin-hero { border:1px solid #dadce0; border-radius:28px; padding:26px; box-shadow:0 2px 14px rgba(60,64,67,.1); margin-bottom:18px; }
    .admin-title { margin:0 0 8px; font-size:34px; }
    .admin-desc { margin:0; color:#5f6368; line-height:1.6; }
    .admin-toolbar { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:18px; flex-wrap:wrap; }
    .admin-status { color:#5f6368; font-size:13px; }
    .admin-btn,.small-btn { border:1px solid #dadce0; background:#fff; border-radius:999px; cursor:pointer; font-weight:800; }
    .admin-btn { padding:9px 13px; font-size:13px; }
    .admin-btn.primary,.small-btn.primary { background:#1a73e8; color:#fff; border-color:#1a73e8; }
    .small-btn.warning { background:#fbbc05; border-color:#fbbc05; color:#202124; }
    .small-btn.danger { background:#c5221f; border-color:#c5221f; color:#fff; }
    .small-btn { padding:6px 9px; font-size:12px; white-space:nowrap; }
    .summary-grid { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-bottom:18px; }
    .summary-card { border:1px solid #dadce0; border-radius:22px; padding:18px; box-shadow:0 2px 10px rgba(60,64,67,.08); }
    .summary-label { font-size:13px; color:#5f6368; margin-bottom:8px; }
    .summary-value { font-size:30px; font-weight:900; color:#1a73e8; }
    .summary-sub { margin-top:6px; font-size:12px; color:#5f6368; line-height:1.4; }
    .admin-section { border:1px solid #dadce0; border-radius:26px; box-shadow:0 2px 12px rgba(60,64,67,.08); margin-bottom:18px; overflow:hidden; }
    .section-head { padding:18px 20px; background:#f8fafd; border-bottom:1px solid #eceff1; display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; }
    .section-title { margin:0; font-size:21px; }
    .section-desc { margin:4px 0 0; color:#5f6368; font-size:13px; line-height:1.5; }
    .section-body { padding:18px 20px; }
    .important-list { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:12px; }
    .important-card { border-radius:20px; border:1px solid #dadce0; padding:15px; background:#fff; }
    .important-card.good { background:#e6f4ea; border-color:#ceead6; }
    .important-card.warning { background:#fef7e0; border-color:#fde293; }
    .important-card.danger { background:#fce8e6; border-color:#fad2cf; }
    .important-card.info { background:#e8f0fe; border-color:#d2e3fc; }
    .important-card.muted { background:#f1f3f4; border-color:#e8eaed; }
    .important-title { font-weight:900; margin-bottom:6px; }
    .important-message { color:#5f6368; font-size:13px; line-height:1.5; }
    .two-column { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
    .bar-list,.mini-list { display:flex; flex-direction:column; gap:10px; }
    .bar-item,.mini-item { border:1px solid #eceff1; border-radius:16px; padding:12px; background:#fafafa; }
    .bar-head,.mini-item { display:flex; justify-content:space-between; gap:10px; align-items:center; }
    .bar-head { margin-bottom:8px; font-size:13px; font-weight:900; }
    .bar-track { height:14px; border-radius:999px; background:#e8eaed; overflow:hidden; }
    .bar-fill { height:100%; width:0%; border-radius:999px; background:#1a73e8; transition:width .45s ease; }
    .mini-title { font-weight:900; font-size:14px; }
    .mini-sub { color:#5f6368; font-size:12px; margin-top:3px; line-height:1.4; }
    .mini-count { font-size:20px; font-weight:900; color:#1a73e8; }
    .table-wrap { overflow-x:auto; }
    .admin-table { width:100%; border-collapse:collapse; min-width:940px; }
    .admin-table th,.admin-table td { border-bottom:1px solid #eceff1; padding:12px 10px; text-align:left; font-size:13px; vertical-align:top; }
    .admin-table th { color:#5f6368; background:#fafafa; font-weight:900; }
    .status-badge { display:inline-flex; border-radius:999px; padding:5px 9px; font-size:12px; font-weight:900; white-space:nowrap; }
    .status-active,.status-approved { background:#e6f4ea; color:#137333; }
    .status-restricted,.status-pending { background:#fef7e0; color:#b06000; }
    .status-deleted,.status-rejected { background:#fce8e6; color:#c5221f; }
    .action-row { display:flex; gap:6px; flex-wrap:wrap; }
    .empty-box { border:1px dashed #c7cdd1; border-radius:18px; padding:22px; text-align:center; color:#5f6368; background:#f8fafd; }
    .promo-content { max-width:360px; white-space:pre-wrap; word-break:keep-all; line-height:1.5; }
    .promo-thumb { width:120px; height:76px; object-fit:cover; border-radius:12px; border:1px solid #dadce0; cursor:pointer; background:#f8fafd; }
    .link-text { color:#1a73e8; font-weight:900; text-decoration:none; cursor:pointer; }
    @media(max-width:960px){ .summary-grid{grid-template-columns:repeat(2,1fr);} .important-list,.two-column{grid-template-columns:1fr;} }
    @media(max-width:640px){ .summary-grid{grid-template-columns:1fr;} .admin-title{font-size:28px;} .admin-main{padding:20px 12px 36px;} }
  </style>
</head>
<body>
  <div class="page">
    <header class="topbar">
      <div class="topbar-right">
        <button class="top-btn" onclick="location.href='/'">메인으로</button>
        <button class="top-btn" onclick="location.href='/promo/request'">홍보 신청 페이지</button>
        <button class="top-btn" onclick="location.href='/logout'">로그아웃</button>
      </div>
    </header>

    <main class="admin-main">
      <section class="admin-hero">
        <h1 class="admin-title">광글 관리자 페이지</h1>
        <p class="admin-desc">홍보 승인/거절, 계정 제재/삭제, 사이트 이용 통계, 중요 운영 정보를 관리합니다.</p>
      </section>

      <div class="admin-toolbar">
        <div id="adminStatus" class="admin-status">관리자 데이터를 불러오는 중...</div>
        <div>
          <button class="admin-btn" onclick="location.href='/promo/request'">홍보 신청 테스트</button>
          <button class="admin-btn primary" onclick="loadDashboard()">새로고침</button>
        </div>
      </div>

      <section class="summary-grid">
        <div class="summary-card"><div class="summary-label">전체 계정</div><div id="totalUsers" class="summary-value">-</div><div id="userSub" class="summary-sub">학생 / 선생님</div></div>
        <div class="summary-card"><div class="summary-label">오늘 사용 기록</div><div id="todayUsage" class="summary-value">-</div><div id="todayUsageSub" class="summary-sub">검색 / 챗봇 / 게임</div></div>
        <div class="summary-card"><div class="summary-label">승인 대기 홍보</div><div id="pendingPromos" class="summary-value">-</div><div id="promoSub" class="summary-sub">승인된 홍보 포함</div></div>
        <div class="summary-card"><div class="summary-label">제재 / 삭제 계정</div><div id="restrictedUsers" class="summary-value">-</div><div id="restrictedSub" class="summary-sub">운영 주의 필요</div></div>
      </section>

      <section class="admin-section">
        <div class="section-head"><div><h2 class="section-title">중요 운영 정보</h2><p class="section-desc">관리자가 먼저 봐야 할 정보를 자동으로 보여줍니다.</p></div></div>
        <div class="section-body"><div id="importantList" class="important-list"></div></div>
      </section>

      <section class="two-column">
        <div class="admin-section">
          <div class="section-head"><div><h2 class="section-title">많이 이용한 도구</h2><p class="section-desc">최근 7일 기준 막대그래프입니다.</p></div></div>
          <div class="section-body"><div id="topTools" class="bar-list"></div></div>
        </div>
        <div class="admin-section">
          <div class="section-head"><div><h2 class="section-title">최근 7일 사용 기록</h2><p class="section-desc">5초마다 자동 갱신되는 사용량 막대그래프입니다.</p></div></div>
          <div class="section-body"><div id="dailyUsage" class="bar-list"></div></div>
        </div>
      </section>

      <section class="admin-section">
        <div class="section-head"><div><h2 class="section-title">홍보 신청 관리</h2><p class="section-desc">홍보물 이미지를 확인하고 승인 또는 거절합니다.</p></div></div>
        <div class="section-body">
          <div class="table-wrap">
            <table class="admin-table">
              <thead><tr><th>ID</th><th>요청자</th><th>홍보 내용</th><th>사유</th><th>홍보물</th><th>상태</th><th>요청일</th><th>관리</th></tr></thead>
              <tbody id="promoTableBody"></tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="admin-section">
        <div class="section-head"><div><h2 class="section-title">계정 관리</h2><p class="section-desc">학생/선생님 계정을 제재, 제재 해제, 삭제 처리할 수 있습니다.</p></div></div>
        <div class="section-body">
          <div class="table-wrap">
            <table class="admin-table">
              <thead><tr><th>ID</th><th>역할</th><th>이름</th><th>아이디</th><th>학번/교사용 ID</th><th>상태</th><th>가입일</th><th>관리</th></tr></thead>
              <tbody id="userTableBody"></tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="admin-section">
        <div class="section-head"><div><h2 class="section-title">최근 상세 로그</h2><p class="section-desc">최근 기능 사용 내역입니다.</p></div></div>
        <div class="section-body"><div id="recentEvents" class="mini-list"></div></div>
      </section>
    </main>
  </div>

  <script>
    function safeText(value){ return value===null||value===undefined||value==="" ? "-" : String(value); }
    function roleLabel(role){ if(role==="student")return"학생"; if(role==="teacher")return"선생님"; if(role==="admin")return"관리자"; return role||"-"; }

    function statusBadge(status){
      const labelMap={active:"정상",restricted:"제재",deleted:"삭제",pending:"대기",approved:"승인",rejected:"거절"};
      const classMap={active:"status-active",restricted:"status-restricted",deleted:"status-deleted",pending:"status-pending",approved:"status-approved",rejected:"status-rejected"};
      const span=document.createElement("span");
      span.className=`status-badge ${classMap[status]||"status-active"}`;
      span.textContent=labelMap[status]||status||"-";
      return span;
    }

    function createEmptyRow(colspan,message){
      const tr=document.createElement("tr");
      const td=document.createElement("td");
      td.colSpan=colspan;
      td.innerHTML=`<div class="empty-box">${message}</div>`;
      tr.appendChild(td);
      return tr;
    }

    function renderBarList(containerId,rows,labelKey,valueKey,emptyMessage){
      const box=document.getElementById(containerId);
      box.innerHTML="";
      if(!rows||rows.length===0){ box.innerHTML=`<div class="empty-box">${emptyMessage}</div>`; return; }
      const maxValue=Math.max(...rows.map(row=>Number(row[valueKey])||0),1);
      rows.forEach(row=>{
        const value=Number(row[valueKey])||0;
        const percent=Math.max(4,Math.round((value/maxValue)*100));
        const item=document.createElement("div");
        item.className="bar-item";
        item.innerHTML=`
          <div class="bar-head"><span></span><span>${value}회</span></div>
          <div class="bar-track"><div class="bar-fill" style="width:${percent}%"></div></div>
        `;
        item.querySelector(".bar-head span").textContent=row[labelKey];
        box.appendChild(item);
      });
    }

    function renderSummary(summary){
      document.getElementById("totalUsers").textContent=summary.total_users;
      document.getElementById("userSub").textContent=`학생 ${summary.student_count}명 · 선생님 ${summary.teacher_count}명 · 정상 ${summary.active_count}명`;
      document.getElementById("todayUsage").textContent=summary.today_total_usage;
      document.getElementById("todayUsageSub").textContent=`검색 ${summary.today_search}회 · 챗봇 ${summary.today_chat}회 · 게임 ${summary.today_game}회`;
      document.getElementById("pendingPromos").textContent=summary.pending_promos;
      document.getElementById("promoSub").textContent=`승인된 홍보 ${summary.approved_promos}개`;
      document.getElementById("restrictedUsers").textContent=summary.restricted_count+summary.deleted_count;
      document.getElementById("restrictedSub").textContent=`제재 ${summary.restricted_count}명 · 삭제 ${summary.deleted_count}명`;
    }

    function renderImportant(cards){
      const box=document.getElementById("importantList");
      box.innerHTML="";
      if(!cards||cards.length===0){ box.innerHTML=`<div class="empty-box">중요 알림이 없습니다.</div>`; return; }
      cards.forEach(card=>{
        const div=document.createElement("div");
        div.className=`important-card ${card.level||"info"}`;
        div.innerHTML=`<div class="important-title"></div><div class="important-message"></div>`;
        div.querySelector(".important-title").textContent=card.title;
        div.querySelector(".important-message").textContent=card.message;
        box.appendChild(div);
      });
    }

    function renderPromoRequests(list){
      const tbody=document.getElementById("promoTableBody");
      tbody.innerHTML="";
      if(!list||list.length===0){ tbody.appendChild(createEmptyRow(8,"홍보 신청이 없습니다.")); return; }

      list.forEach(item=>{
        const tr=document.createElement("tr");

        const tdId=document.createElement("td");
        tdId.textContent=item.id;

        const tdRequester=document.createElement("td");
        tdRequester.textContent=`${safeText(item.requester_name)} (${roleLabel(item.requester_role)})`;

        const tdWhat=document.createElement("td");
        const what=document.createElement("div");
        what.className="mini-title";
        what.textContent=item.promo_what||"-";
        const detailLink=document.createElement("a");
        detailLink.className="link-text";
        detailLink.href=`/promotion/${item.id}`;
        detailLink.target="_blank";
        detailLink.textContent="상세 보기";
        tdWhat.appendChild(what);
        tdWhat.appendChild(detailLink);

        const tdReason=document.createElement("td");
        const reason=document.createElement("div");
        reason.className="promo-content";
        reason.textContent=item.promo_reason||"-";
        tdReason.appendChild(reason);

        const tdImage=document.createElement("td");
        if(item.image_path){
          const img=document.createElement("img");
          img.className="promo-thumb";
          img.src=item.image_path;
          img.alt="홍보물";
          img.onclick=()=>window.open(`/promotion/${item.id}`,"_blank");
          tdImage.appendChild(img);
        }else{
          tdImage.textContent="-";
        }

        const tdStatus=document.createElement("td");
        tdStatus.appendChild(statusBadge(item.status));
        if(item.admin_note){
          const note=document.createElement("div");
          note.className="mini-sub";
          note.textContent=item.admin_note;
          tdStatus.appendChild(note);
        }

        const tdDate=document.createElement("td");
        tdDate.textContent=item.created_at ? item.created_at.slice(0,16).replace("T"," ") : "-";

        const tdAction=document.createElement("td");
        const actions=document.createElement("div");
        actions.className="action-row";

        if(item.status==="pending"){
          const approveBtn=document.createElement("button");
          approveBtn.className="small-btn primary";
          approveBtn.textContent="승인";
          approveBtn.onclick=()=>handlePromoAction(item.id,"approve");

          const rejectBtn=document.createElement("button");
          rejectBtn.className="small-btn danger";
          rejectBtn.textContent="거절";
          rejectBtn.onclick=()=>handlePromoAction(item.id,"reject");

          actions.appendChild(approveBtn);
          actions.appendChild(rejectBtn);
        }else{
          const doneBtn=document.createElement("button");
          doneBtn.className="small-btn";
          doneBtn.textContent="처리 완료";
          doneBtn.disabled=true;
          actions.appendChild(doneBtn);
        }

        tdAction.appendChild(actions);

        [tdId,tdRequester,tdWhat,tdReason,tdImage,tdStatus,tdDate,tdAction].forEach(td=>tr.appendChild(td));
        tbody.appendChild(tr);
      });
    }

    function renderUsers(list){
      const tbody=document.getElementById("userTableBody");
      tbody.innerHTML="";
      if(!list||list.length===0){ tbody.appendChild(createEmptyRow(8,"회원 계정이 없습니다.")); return; }

      list.forEach(user=>{
        const tr=document.createElement("tr");

        [user.id,roleLabel(user.role),user.student_name,user.username,user.student_id].forEach(value=>{
          const td=document.createElement("td");
          td.textContent=safeText(value);
          tr.appendChild(td);
        });

        const tdStatus=document.createElement("td");
        tdStatus.appendChild(statusBadge(user.account_status));
        if(user.status_reason){
          const reason=document.createElement("div");
          reason.className="mini-sub";
          reason.textContent=user.status_reason;
          tdStatus.appendChild(reason);
        }
        tr.appendChild(tdStatus);

        const tdDate=document.createElement("td");
        tdDate.textContent=user.created_at ? user.created_at.slice(0,16).replace("T"," ") : "-";
        tr.appendChild(tdDate);

        const tdAction=document.createElement("td");
        const actions=document.createElement("div");
        actions.className="action-row";

        if(user.account_status==="active"){
          const restrictBtn=document.createElement("button");
          restrictBtn.className="small-btn warning";
          restrictBtn.textContent="제재";
          restrictBtn.onclick=()=>handleUserAction(user.id,"restrict");

          const deleteBtn=document.createElement("button");
          deleteBtn.className="small-btn danger";
          deleteBtn.textContent="삭제";
          deleteBtn.onclick=()=>handleUserAction(user.id,"delete");

          actions.appendChild(restrictBtn);
          actions.appendChild(deleteBtn);
        }else if(user.account_status==="restricted"){
          const unrestrictBtn=document.createElement("button");
          unrestrictBtn.className="small-btn primary";
          unrestrictBtn.textContent="제재 해제";
          unrestrictBtn.onclick=()=>handleUserAction(user.id,"unrestrict");

          const deleteBtn=document.createElement("button");
          deleteBtn.className="small-btn danger";
          deleteBtn.textContent="삭제";
          deleteBtn.onclick=()=>handleUserAction(user.id,"delete");

          actions.appendChild(unrestrictBtn);
          actions.appendChild(deleteBtn);
        }else{
          const deleted=document.createElement("button");
          deleted.className="small-btn";
          deleted.textContent="삭제됨";
          deleted.disabled=true;
          actions.appendChild(deleted);
        }

        tdAction.appendChild(actions);
        tr.appendChild(tdAction);
        tbody.appendChild(tr);
      });
    }

    function renderRecentEvents(list){
      const box=document.getElementById("recentEvents");
      box.innerHTML="";
      if(!list||list.length===0){ box.innerHTML=`<div class="empty-box">최근 사용 기록이 없습니다.</div>`; return; }

      list.forEach(event=>{
        const div=document.createElement("div");
        div.className="mini-item";
        div.innerHTML=`
          <div>
            <div class="mini-title"></div>
            <div class="mini-sub"></div>
          </div>
          <div class="mini-count">#</div>
        `;
        div.querySelector(".mini-title").textContent=`${event.tool_name} · ${event.event_type}`;
        div.querySelector(".mini-sub").textContent=`${roleLabel(event.user_role)} · ${event.created_at ? event.created_at.slice(0,19).replace("T"," ") : "-"}`;
        box.appendChild(div);
      });
    }

    async function handleUserAction(userId,action){
      let reason="";

      if(action==="restrict"){
        reason=prompt("제재 사유를 입력하세요.","관리자에 의해 계정 이용이 제한되었습니다.");
        if(reason===null)return;
      }

      if(action==="delete"){
        const ok=confirm("정말 이 계정을 삭제 처리할까요? 삭제된 계정은 로그인할 수 없습니다.");
        if(!ok)return;
        reason=prompt("삭제 사유를 입력하세요.","관리자에 의해 계정이 삭제 처리되었습니다.");
        if(reason===null)return;
      }

      if(action==="unrestrict"){
        const ok=confirm("이 계정의 제재를 해제할까요?");
        if(!ok)return;
      }

      try{
        const res=await fetch("/api/admin/user-action",{
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({targetUserId:userId,action,reason})
        });

        const data=await res.json();
        alert(data.message||"처리되었습니다.");

        if(data.success&&data.data) renderDashboard(data.data);
        else loadDashboard();
      }catch(error){
        alert("계정 처리 중 오류가 발생했습니다.");
      }
    }

    async function handlePromoAction(promoId,action){
      let note="";

      if(action==="approve"){
        note=prompt("승인 메모를 입력하세요. 비워도 됩니다.","");
        if(note===null)return;
      }

      if(action==="reject"){
        note=prompt("거절 사유를 입력하세요.","홍보 기준에 맞지 않아 거절되었습니다.");
        if(note===null)return;
      }

      try{
        const res=await fetch("/api/admin/promo-action",{
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({promoRequestId:promoId,action,adminNote:note})
        });

        const data=await res.json();
        alert(data.message||"처리되었습니다.");

        if(data.success&&data.data) renderDashboard(data.data);
        else loadDashboard();
      }catch(error){
        alert("홍보 신청 처리 중 오류가 발생했습니다.");
      }
    }

    function renderDashboard(data){
      renderSummary(data.summary);
      renderImportant(data.important_cards);
      renderBarList("topTools",data.top_tools,"tool_name","count","아직 사용 기록이 없습니다.");
      renderBarList("dailyUsage",data.daily_usage,"day","count","최근 사용 추이가 없습니다.");
      renderPromoRequests(data.promo_requests);
      renderUsers(data.users);
      renderRecentEvents(data.recent_events);
      document.getElementById("adminStatus").textContent=`마지막 업데이트: ${data.updated_at} · 5초마다 자동 갱신`;
    }

    async function loadDashboard(){
      try{
        const res=await fetch("/api/admin/dashboard");
        const data=await res.json();

        if(!data.success){
          document.getElementById("adminStatus").textContent=data.message||"관리자 데이터를 불러오지 못했습니다.";
          return;
        }

        renderDashboard(data.data);
      }catch(error){
        document.getElementById("adminStatus").textContent="관리자 데이터를 불러오는 중 오류가 발생했습니다.";
      }
    }

    loadDashboard();
    setInterval(loadDashboard,5000);
  </script>
</body>
</html>
'''


PROMO_REQUEST_HTML = r'''<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>광글 홍보 신청</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
  <style>
    body { background:#fff; }
    .promo-main { max-width:720px; margin:0 auto; padding:36px 18px 52px; }
    .promo-card { border:1px solid #dadce0; border-radius:28px; padding:28px; background:#fff; box-shadow:0 2px 14px rgba(60,64,67,.1); }
    .promo-title { margin:0 0 8px; font-size:32px; color:#202124; }
    .promo-desc { margin:0 0 20px; color:#5f6368; font-size:14px; line-height:1.6; }
    .promo-info { margin-bottom:18px; padding:14px; border-radius:18px; background:#f8fafd; border:1px solid #eceff1; color:#5f6368; font-size:13px; line-height:1.6; }
    .promo-form { display:flex; flex-direction:column; gap:13px; }
    .promo-label { font-size:13px; font-weight:900; color:#3c4043; }
    .promo-input,.promo-textarea { width:100%; border:1px solid #dadce0; border-radius:16px; padding:13px 14px; font-size:15px; outline:none; box-sizing:border-box; background:#fff; }
    .promo-input { height:48px; }
    .promo-textarea { min-height:190px; resize:vertical; line-height:1.6; }
    .promo-input:focus,.promo-textarea:focus { border-color:#1a73e8; }
    .file-box { border:1px dashed #c7cdd1; border-radius:18px; padding:16px; background:#f8fafd; }
    .file-help { margin-top:8px; color:#5f6368; font-size:12px; line-height:1.5; }
    .preview-img { display:none; width:100%; max-height:360px; object-fit:contain; border-radius:18px; margin-top:12px; border:1px solid #eceff1; background:#fff; }
    .promo-btn { height:50px; border:none; border-radius:999px; background:#1a73e8; color:#fff; font-size:15px; font-weight:900; cursor:pointer; margin-top:8px; }
    .promo-links { margin-top:18px; display:flex; justify-content:space-between; gap:10px; font-size:14px; }
    .promo-links a { color:#1a73e8; text-decoration:none; font-weight:900; }
    .flash-wrap { margin-bottom:14px; }
    .flash-message { padding:12px 14px; border-radius:16px; background:#e8f0fe; color:#174ea6; font-size:14px; margin-bottom:8px; }
    .count-hint { text-align:right; color:#5f6368; font-size:12px; }
  </style>
</head>
<body>
  <div class="page">
    <header class="topbar">
      <div class="topbar-right">
        <button class="top-btn" onclick="location.href='/'">메인으로</button>
        {% if current_user and current_user["role"] == "admin" %}
          <button class="top-btn" onclick="location.href='/admin'">관리자</button>
        {% endif %}
        {% if current_user %}
          <button class="top-btn" onclick="location.href='/logout'">로그아웃</button>
        {% else %}
          <button class="top-btn" onclick="location.href='/login'">로그인</button>
        {% endif %}
      </div>
    </header>

    <main class="promo-main">
      {% with messages = get_flashed_messages() %}
        {% if messages %}
          <div class="flash-wrap">
            {% for message in messages %}
              <div class="flash-message">{{ message }}</div>
            {% endfor %}
          </div>
        {% endif %}
      {% endwith %}

      <section class="promo-card">
        <h1 class="promo-title">홍보 신청</h1>
        <p class="promo-desc">광글 메인 화면 팝업에 띄우고 싶은 홍보물을 관리자에게 신청할 수 있습니다.</p>

        <div class="promo-info">
          신청한 홍보물은 바로 공개되지 않고, 관리자가 승인한 뒤 메인 화면 팝업에 랜덤으로 표시됩니다.
          이미지는 5MB 이하의 png, jpg, jpeg, webp, gif 파일만 가능합니다.
        </div>

        <form class="promo-form" method="POST" action="/promo/request" enctype="multipart/form-data">
          <label class="promo-label" for="promoWhat">어떤 걸 홍보할 건가요?</label>
          <input id="promoWhat" name="promoWhat" class="promo-input" type="text" placeholder="예: 컴퓨터 동아리 부스, 학급 행사, 캠페인" maxlength="80" required>

          <label class="promo-label" for="promoReason">홍보 사유</label>
          <textarea id="promoReason" name="promoReason" class="promo-textarea" placeholder="왜 홍보가 필요한지, 누구에게 알리고 싶은지, 어떤 효과를 기대하는지 작성해주세요." maxlength="1000" required></textarea>
          <div id="reasonCount" class="count-hint">0 / 1000</div>

          <label class="promo-label" for="promoImage">홍보물 이미지</label>
          <div class="file-box">
            <input id="promoImage" name="promoImage" type="file" accept="image/png,image/jpeg,image/webp,image/gif" required>
            <div class="file-help">권장 크기: 가로형 이미지 또는 정사각형 이미지 / 최대 용량: 5MB</div>
            <img id="previewImg" class="preview-img" alt="홍보물 미리보기">
          </div>

          <button class="promo-btn" type="submit">관리자에게 홍보 신청 보내기</button>
        </form>

        <div class="promo-links">
          <a href="/">메인으로</a>
          <a href="/chatbot">챗봇으로 이동</a>
        </div>
      </section>
    </main>
  </div>

  <script>
    const reason=document.getElementById("promoReason");
    const count=document.getElementById("reasonCount");
    const promoImage=document.getElementById("promoImage");
    const previewImg=document.getElementById("previewImg");

    reason.addEventListener("input",()=>{ count.textContent=`${reason.value.length} / 1000`; });

    promoImage.addEventListener("change",()=>{
      const file=promoImage.files[0];

      if(!file){
        previewImg.style.display="none";
        previewImg.src="";
        return;
      }

      if(file.size > 5 * 1024 * 1024){
        alert("이미지는 5MB 이하만 업로드할 수 있습니다.");
        promoImage.value="";
        previewImg.style.display="none";
        previewImg.src="";
        return;
      }

      const url=URL.createObjectURL(file);
      previewImg.src=url;
      previewImg.style.display="block";
    });
  </script>
</body>
</html>
'''


PROMOTION_DETAIL_HTML = r'''<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{{ promo["promo_what"] }} - 광글 홍보</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
  <style>
    body { background:#fff; color:#202124; }
    .detail-main { max-width:880px; margin:0 auto; padding:34px 18px 52px; }
    .detail-card { border:1px solid #dadce0; border-radius:30px; overflow:hidden; background:#fff; box-shadow:0 2px 16px rgba(60,64,67,.12); }
    .detail-image { width:100%; max-height:620px; object-fit:contain; background:#f8fafd; border-bottom:1px solid #eceff1; display:block; }
    .detail-content { padding:26px; }
    .detail-badge { display:inline-flex; border-radius:999px; background:#e8f0fe; color:#174ea6; padding:7px 11px; font-size:12px; font-weight:900; margin-bottom:12px; }
    .detail-title { margin:0 0 12px; font-size:32px; color:#202124; }
    .detail-meta { color:#5f6368; font-size:13px; margin-bottom:20px; line-height:1.5; }
    .detail-section-title { margin:20px 0 8px; font-size:18px; color:#202124; }
    .detail-text { color:#3c4043; font-size:15px; line-height:1.75; white-space:pre-wrap; word-break:keep-all; }
    .detail-actions { display:flex; gap:10px; margin-top:24px; flex-wrap:wrap; }
    .detail-btn { border:1px solid #dadce0; background:#fff; color:#3c4043; border-radius:999px; padding:10px 14px; cursor:pointer; font-weight:900; font-size:14px; }
    .detail-btn.primary { background:#1a73e8; color:#fff; border-color:#1a73e8; }
    @media(max-width:640px){ .detail-title{font-size:26px;} .detail-content{padding:20px;} }
  </style>
</head>
<body>
  <div class="page">
    <header class="topbar">
      <div class="topbar-right">
        <button class="top-btn" onclick="location.href='/'">메인으로</button>
        <button class="top-btn" onclick="location.href='/promo/request'">홍보 신청</button>
        {% if current_user and current_user["role"] == "admin" %}
          <button class="top-btn" onclick="location.href='/admin'">관리자</button>
        {% endif %}
      </div>
    </header>

    <main class="detail-main">
      <article class="detail-card">
        {% if promo["image_path"] %}
          <img class="detail-image" src="{{ promo['image_path'] }}" alt="홍보물 이미지">
        {% endif %}

        <div class="detail-content">
          <div class="detail-badge">광글 홍보</div>
          <h1 class="detail-title">{{ promo["promo_what"] }}</h1>

          <div class="detail-meta">
            요청자: {{ promo["requester_name"] }}
            · 공개 상태: {{ promo["status"] }}
            · 등록일: {{ promo["created_at"][:10] }}
          </div>

          <h2 class="detail-section-title">홍보 사유</h2>
          <div class="detail-text">{{ promo["promo_reason"] }}</div>

          <div class="detail-actions">
            <button class="detail-btn primary" onclick="location.href='/'">메인으로 돌아가기</button>
            <button class="detail-btn" onclick="location.href='/promo/request'">나도 홍보 신청하기</button>
          </div>
        </div>
      </article>
    </main>
  </div>
</body>
</html>
'''


PROMO_POPUP_CSS = r'''.promo-popup-overlay {
  position: fixed;
  inset: 0;
  background: rgba(32, 33, 36, 0.42);
  z-index: 99999;
  display: none;
  align-items: center;
  justify-content: center;
  padding: 18px;
}

.promo-popup-overlay.show {
  display: flex;
}

.promo-popup-card {
  width: 100%;
  max-width: 460px;
  background: #ffffff;
  border-radius: 28px;
  overflow: hidden;
  box-shadow: 0 12px 40px rgba(32, 33, 36, 0.28);
  border: 1px solid #dadce0;
  animation: promoPop 0.2s ease-out;
}

@keyframes promoPop {
  from {
    transform: scale(0.92);
    opacity: 0;
  }
  to {
    transform: scale(1);
    opacity: 1;
  }
}

.promo-popup-image {
  width: 100%;
  max-height: 360px;
  object-fit: contain;
  background: #f8fafd;
  cursor: pointer;
  display: block;
}

.promo-popup-body {
  padding: 18px;
}

.promo-popup-badge {
  display: inline-flex;
  padding: 6px 10px;
  border-radius: 999px;
  background: #e8f0fe;
  color: #174ea6;
  font-size: 12px;
  font-weight: 900;
  margin-bottom: 10px;
}

.promo-popup-title {
  margin: 0 0 8px;
  font-size: 22px;
  color: #202124;
  line-height: 1.35;
  cursor: pointer;
}

.promo-popup-reason {
  margin: 0;
  color: #5f6368;
  font-size: 13px;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.promo-popup-actions {
  display: flex;
  gap: 8px;
  padding: 0 18px 18px;
  flex-wrap: wrap;
}

.promo-popup-btn {
  border: 1px solid #dadce0;
  background: #ffffff;
  color: #3c4043;
  border-radius: 999px;
  padding: 10px 13px;
  cursor: pointer;
  font-size: 13px;
  font-weight: 900;
}

.promo-popup-btn.primary {
  background: #1a73e8;
  color: #ffffff;
  border-color: #1a73e8;
}
'''


PROMO_POPUP_JS = r'''(function () {
  const PROMO_HIDE_KEY = "gwangle_promo_hide_date";

  function todayKey() {
    return new Date().toISOString().slice(0, 10);
  }

  function shouldShowPromoPopup() {
    return localStorage.getItem(PROMO_HIDE_KEY) !== todayKey();
  }

  function closePromoPopup() {
    const overlay = document.getElementById("gwanglePromoPopupOverlay");
    if (overlay) overlay.classList.remove("show");
  }

  function hidePromoForToday() {
    localStorage.setItem(PROMO_HIDE_KEY, todayKey());
    closePromoPopup();
  }

  function openPromoDetail(promoId) {
    location.href = `/promotion/${promoId}`;
  }

  function createPopup() {
    if (document.getElementById("gwanglePromoPopupOverlay")) {
      return;
    }

    const overlay = document.createElement("div");
    overlay.id = "gwanglePromoPopupOverlay";
    overlay.className = "promo-popup-overlay";

    overlay.innerHTML = `
      <div class="promo-popup-card">
        <img id="gwanglePromoPopupImage" class="promo-popup-image" alt="홍보 팝업 이미지">
        <div class="promo-popup-body">
          <div class="promo-popup-badge">광글 홍보</div>
          <h2 id="gwanglePromoPopupTitle" class="promo-popup-title"></h2>
          <p id="gwanglePromoPopupReason" class="promo-popup-reason"></p>
        </div>
        <div class="promo-popup-actions">
          <button id="gwanglePromoPopupDetailBtn" class="promo-popup-btn primary">자세히 보기</button>
          <button id="gwanglePromoPopupCloseBtn" class="promo-popup-btn">닫기</button>
          <button id="gwanglePromoPopupTodayBtn" class="promo-popup-btn">하루 동안 안 보기</button>
        </div>
      </div>
    `;

    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) {
        closePromoPopup();
      }
    });
  }

  async function loadPromoPopup() {
    if (location.pathname !== "/") {
      return;
    }

    if (!shouldShowPromoPopup()) {
      return;
    }

    try {
      const res = await fetch("/api/promos/approved");
      const data = await res.json();

      if (!data.success || !data.promos || data.promos.length === 0) {
        return;
      }

      createPopup();

      const randomIndex = Math.floor(Math.random() * data.promos.length);
      const promo = data.promos[randomIndex];

      const overlay = document.getElementById("gwanglePromoPopupOverlay");
      const image = document.getElementById("gwanglePromoPopupImage");
      const title = document.getElementById("gwanglePromoPopupTitle");
      const reason = document.getElementById("gwanglePromoPopupReason");
      const detailBtn = document.getElementById("gwanglePromoPopupDetailBtn");
      const closeBtn = document.getElementById("gwanglePromoPopupCloseBtn");
      const todayBtn = document.getElementById("gwanglePromoPopupTodayBtn");

      image.src = promo.image_path;
      title.textContent = promo.promo_what || "광글 홍보";
      reason.textContent = promo.promo_reason || "";

      image.onclick = () => openPromoDetail(promo.id);
      title.onclick = () => openPromoDetail(promo.id);
      detailBtn.onclick = () => openPromoDetail(promo.id);
      closeBtn.onclick = closePromoPopup;
      todayBtn.onclick = hidePromoForToday;

      setTimeout(() => {
        overlay.classList.add("show");
      }, 700);

    } catch (error) {
      console.log("홍보 팝업을 불러오지 못했습니다.");
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", loadPromoPopup);
  } else {
    loadPromoPopup();
  }
})();
'''


def backup_file(path: Path, backup_dir: Path):
    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / path.name)


def patch_app_py():
    if not APP_PATH.exists():
        raise FileNotFoundError("app.py를 찾을 수 없습니다. 이 파일을 Gwangle 폴더 안에서 실행하세요.")

    text = APP_PATH.read_text(encoding="utf-8")

    text = re.sub(
        r"MAX_CONTENT_LENGTH\s*=\s*.*",
        "MAX_CONTENT_LENGTH = 7 * 1024 * 1024",
        text,
        count=1
    )

    if '"promoWhat":' not in text:
        if '"gameType": 30,' in text:
            text = text.replace(
                '"gameType": 30,',
                '"gameType": 30,\n    "promoWhat": 80,\n    "promoReason": 1000,\n    "adminNote": 300,'
            )
        elif "'gameType': 30," in text:
            text = text.replace(
                "'gameType': 30,",
                "'gameType': 30,\n    'promoWhat': 80,\n    'promoReason': 1000,\n    'adminNote': 300,"
            )

    extension_pattern = re.compile(
        r"\n# ={10,}\n# Admin Management[\s\S]*?(?=\nif __name__ == \"__main__\":)",
        re.MULTILINE
    )

    if extension_pattern.search(text):
        text = extension_pattern.sub("\n" + ADMIN_EXTENSION + "\n", text)
    else:
        text = text.replace(
            '\nif __name__ == "__main__":',
            "\n" + ADMIN_EXTENSION + '\n\nif __name__ == "__main__":'
        )

    APP_PATH.write_text(text, encoding="utf-8")


def patch_index_shortcut():
    if not INDEX_PATH.exists():
        print("templates/index.html을 찾지 못해서 즐겨찾기 자동 추가는 건너뜁니다.")
        return

    text = INDEX_PATH.read_text(encoding="utf-8")

    if 'href="/promo/request"' in text or "href='/promo/request'" in text:
        INDEX_PATH.write_text(text, encoding="utf-8")
        return

    shortcut_html = '''
          <a href="/promo/request" class="shortcut-item">
            <div class="shortcut-circle">📢</div>
            <div class="shortcut-label">홍보 신청</div>
          </a>
'''

    calendar_block = re.compile(
        r'(<a href="\{\{ url_for\(\'static\', filename=\'calendar\.html\'\) \}\}" class="shortcut-item">[\s\S]*?</a>)'
    )

    if calendar_block.search(text):
        text = calendar_block.sub(r"\1\n" + shortcut_html, text, count=1)
    else:
        grid_match = re.search(r'(<div class="shortcut-grid">[\s\S]*?)(\s*</div>\s*</section>)', text)
        if grid_match:
            text = text[:grid_match.end(1)] + "\n" + shortcut_html + text[grid_match.end(1):]
        else:
            print("shortcut-grid를 찾지 못해서 즐겨찾기 자동 추가는 건너뜁니다.")

    INDEX_PATH.write_text(text, encoding="utf-8")


def write_files():
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    ADMIN_PATH.write_text(ADMIN_HTML, encoding="utf-8")
    PROMO_REQUEST_PATH.write_text(PROMO_REQUEST_HTML, encoding="utf-8")
    PROMOTION_DETAIL_PATH.write_text(PROMOTION_DETAIL_HTML, encoding="utf-8")
    PROMO_JS_PATH.write_text(PROMO_POPUP_JS, encoding="utf-8")
    PROMO_CSS_PATH.write_text(PROMO_POPUP_CSS, encoding="utf-8")


def main():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BASE_DIR / f"backup_before_promo_admin_{timestamp}"

    for path in [
        APP_PATH,
        INDEX_PATH,
        ADMIN_PATH,
        PROMO_REQUEST_PATH,
        PROMOTION_DETAIL_PATH,
        PROMO_JS_PATH,
        PROMO_CSS_PATH,
    ]:
        backup_file(path, backup_dir)

    patch_app_py()
    patch_index_shortcut()
    write_files()

    print("✅ 홍보 신청/관리자 기능 업데이트 완료")
    print("✅ 홍보 이미지 제한: 5MB")
    print("✅ 관리자 승인/거절 버튼 적용")
    print("✅ 계정 제재/삭제 버튼 적용")
    print("✅ 최근 사용 기록 막대그래프 적용")
    print("✅ 승인된 홍보물 메인 팝업 + 상세 페이지 적용")
    print(f"✅ 기존 파일 백업 위치: {backup_dir}")


if __name__ == "__main__":
    main()