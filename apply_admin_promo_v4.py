from pathlib import Path
import re
import shutil
import datetime

BASE_DIR = Path(__file__).resolve().parent
APP_PATH = BASE_DIR / "app.py"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

INDEX_PATH = TEMPLATES_DIR / "index.html"
ADMIN_PATH = TEMPLATES_DIR / "admin.html"
PROMO_JS_PATH = STATIC_DIR / "promo_popup.js"
PROMO_CSS_PATH = STATIC_DIR / "promo_popup.css"

FORCE_BLOCK = r'''
# ==============================
# GWANGLE ADMIN PROMO V4
# 제재 24시간 / 계정 실제 삭제 / 승인 팝업 노출 / 거절 요청 삭제 / 통계 막대그래프
# ==============================

PROMO_UPLOAD_DIR = os.path.join("static", "uploads", "promos")
PROMO_MAX_IMAGE_BYTES = 20 * 1024 * 1024
PROMO_ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024


def gwangle_v4_init_db():
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

    if not column_exists(conn, "users", "restricted_until"):
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN restricted_until TEXT
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

    promo_columns = [
        ("promo_what", "ALTER TABLE promo_requests ADD COLUMN promo_what TEXT"),
        ("promo_reason", "ALTER TABLE promo_requests ADD COLUMN promo_reason TEXT"),
        ("image_path", "ALTER TABLE promo_requests ADD COLUMN image_path TEXT"),
        ("image_original_name", "ALTER TABLE promo_requests ADD COLUMN image_original_name TEXT"),
        ("image_size_bytes", "ALTER TABLE promo_requests ADD COLUMN image_size_bytes INTEGER"),
    ]

    for column_name, sql in promo_columns:
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


gwangle_v4_init_db()


def gwangle_v4_row_to_dict(row):
    if not row:
        return None
    return {key: row[key] for key in row.keys()}


def gwangle_v4_is_admin():
    user = get_current_user()
    return bool(user and user["role"] == "admin")


def gwangle_v4_admin_json_required():
    if not gwangle_v4_is_admin():
        return jsonify({
            "success": False,
            "message": "관리자 로그인 후 이용할 수 있습니다."
        }), 403
    return None


@app.before_request
def gwangle_v4_account_guard():
    if request.path.startswith("/static"):
        return None

    if request.path in ["/login", "/logout", "/signup", "/signup/step2"]:
        return None

    if session.get("is_admin") is True:
        return None

    user_id = session.get("user_id")
    if not user_id:
        return None

    conn = get_db()
    user = conn.execute("""
        SELECT id, account_status, status_reason, restricted_until
        FROM users
        WHERE id = ?
    """, (user_id,)).fetchone()

    if not user:
        conn.close()
        session.clear()

        if request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "message": "삭제되었거나 존재하지 않는 계정입니다."
            }), 403

        flash("삭제되었거나 존재하지 않는 계정입니다.")
        return redirect(url_for("login_page"))

    if user["account_status"] == "restricted":
        now = datetime.datetime.now()
        restricted_until_text = user["restricted_until"]

        try:
            restricted_until = datetime.datetime.fromisoformat(restricted_until_text) if restricted_until_text else now
        except ValueError:
            restricted_until = now

        if now >= restricted_until:
            conn.execute("""
                UPDATE users
                SET account_status = 'active',
                    status_reason = NULL,
                    restricted_until = NULL
                WHERE id = ?
            """, (user_id,))
            conn.commit()
            conn.close()
            return None

        remain = restricted_until - now
        hours = max(0, remain.seconds // 3600)
        minutes = max(1, (remain.seconds % 3600) // 60)
        reason = user["status_reason"] or "관리자에 의해 24시간 이용이 제한되었습니다."
        message = f"{reason} 남은 시간: 약 {hours}시간 {minutes}분"

        conn.close()

        if request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "message": message
            }), 403

        flash(message)
        return redirect(url_for("login_page"))

    if user["account_status"] != "active":
        conn.close()
        session.clear()

        if request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "message": "이용할 수 없는 계정입니다."
            }), 403

        flash("이용할 수 없는 계정입니다.")
        return redirect(url_for("login_page"))

    conn.close()
    return None


def gwangle_v4_safe_json_dumps(data):
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return "{}"


def gwangle_v4_log_usage(event_type, tool_name, detail=None):
    try:
        if request.path.startswith("/api/admin"):
            return

        if request.path.startswith("/static"):
            return

        user_id = session.get("user_id")
        user_role = session.get("user_role", "guest")

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
            user_role,
            event_type,
            tool_name,
            gwangle_v4_safe_json_dumps(detail or {}),
            datetime.datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()

    except Exception:
        pass


@app.after_request
def gwangle_v4_inject_popup_assets(response):
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
def gwangle_v4_usage_logger(response):
    try:
        if response.status_code >= 400:
            return response

        if request.path == "/api/search":
            query = request.args.get("query", "")
            if query:
                gwangle_v4_log_usage("search", "검색", {"query": query})

        elif request.path == "/api/chat" and request.method == "POST":
            data = request.get_json(silent=True) or {}
            message = data.get("message", "")
            gwangle_v4_log_usage("chat", "챗봇", {"message_length": len(message)})

        elif request.path == "/api/meal":
            gwangle_v4_log_usage("tool", "급식", {"mode": request.args.get("mode", "")})

        elif request.path == "/api/timetable":
            gwangle_v4_log_usage("tool", "시간표", {
                "grade": request.args.get("grade", ""),
                "class": request.args.get("class", "")
            })

        elif request.path == "/api/game/start":
            data = request.get_json(silent=True) or {}
            gwangle_v4_log_usage("game", "미니게임 시작", {"game_type": data.get("gameType", "")})

        elif request.path == "/api/game/submit":
            data = request.get_json(silent=True) or {}
            gwangle_v4_log_usage("game", "미니게임 점수 제출", {
                "game_type": data.get("gameType", ""),
                "score": data.get("score", 0)
            })

        elif request.path == "/teachers":
            gwangle_v4_log_usage("page", "선생님 목록", {})

        elif request.path == "/game":
            gwangle_v4_log_usage("page", "미니게임 페이지", {})

        elif request.path == "/profile":
            gwangle_v4_log_usage("page", "프로필", {})

        elif request.path == "/promo/request" and request.method == "POST":
            gwangle_v4_log_usage("promo", "홍보 신청", {})

        elif request.path.startswith("/promotion/"):
            gwangle_v4_log_usage("promo", "홍보 상세", {"path": request.path})

    except Exception:
        pass

    return response


def gwangle_v4_allowed_promo_image(filename):
    if not filename or "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in PROMO_ALLOWED_EXTENSIONS


def gwangle_v4_save_promo_image(file_storage):
    if not file_storage or not file_storage.filename:
        return None, None, None, "홍보물 이미지를 업로드해주세요."

    original_name = file_storage.filename

    if not gwangle_v4_allowed_promo_image(original_name):
        return None, None, None, "홍보물 이미지는 png, jpg, jpeg, webp, gif 파일만 업로드할 수 있습니다."

    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)

    if size <= 0:
        return None, None, None, "비어 있는 파일은 업로드할 수 없습니다."

    if size > PROMO_MAX_IMAGE_BYTES:
        return None, None, None, "홍보물 이미지는 20MB 이하로 업로드해주세요."

    ext = original_name.rsplit(".", 1)[1].lower()
    saved_name = f"{uuid.uuid4().hex}.{ext}"
    save_path = os.path.join(PROMO_UPLOAD_DIR, saved_name)

    file_storage.save(save_path)

    web_path = f"/static/uploads/promos/{saved_name}"
    return web_path, original_name, size, None


def gwangle_v4_promo_request_page():
    current_user = get_current_user()

    if not current_user:
        flash("로그인 후 홍보 신청을 할 수 있습니다.")
        return redirect(url_for("login_page"))

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

        image_path, original_name, image_size, image_error = gwangle_v4_save_promo_image(promo_image)

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


def gwangle_v4_approved_promos_api():
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
        ORDER BY RANDOM()
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


def gwangle_v4_promotion_detail_page(promo_id):
    conn = get_db()
    promo = conn.execute("""
        SELECT
            id,
            requester_role,
            requester_name,
            COALESCE(promo_what, title) AS promo_what,
            COALESCE(promo_reason, content) AS promo_reason,
            image_path,
            status,
            created_at
        FROM promo_requests
        WHERE id = ?
    """, (promo_id,)).fetchone()
    conn.close()

    if not promo:
        flash("존재하지 않는 홍보물입니다.")
        return redirect(url_for("home"))

    current_user = get_current_user()
    is_admin = current_user and current_user["role"] == "admin"

    if promo["status"] != "approved" and not is_admin:
        flash("아직 승인되지 않은 홍보물입니다.")
        return redirect(url_for("home"))

    if promo["image_path"]:
        return redirect(promo["image_path"])

    flash("홍보물 이미지가 없습니다.")
    return redirect(url_for("home"))


def gwangle_v4_admin_page():
    if not gwangle_v4_is_admin():
        flash("관리자만 접근할 수 있습니다.")
        return redirect(url_for("login_page"))
    return render_template("admin.html")


def gwangle_v4_dashboard_data():
    conn = get_db()

    now = datetime.datetime.now()
    today_prefix = datetime.date.today().isoformat()
    week_ago = (now - datetime.timedelta(days=7)).isoformat()

    def count_one(sql, params=()):
        return conn.execute(sql, params).fetchone()["cnt"]

    total_users = count_one("SELECT COUNT(*) AS cnt FROM users")
    student_count = count_one("SELECT COUNT(*) AS cnt FROM users WHERE role = 'student'")
    teacher_count = count_one("SELECT COUNT(*) AS cnt FROM users WHERE role = 'teacher'")
    active_count = count_one("SELECT COUNT(*) AS cnt FROM users WHERE account_status = 'active'")
    restricted_count = count_one("SELECT COUNT(*) AS cnt FROM users WHERE account_status = 'restricted'")
    pending_promos = count_one("SELECT COUNT(*) AS cnt FROM promo_requests WHERE status = 'pending'")
    approved_promos = count_one("SELECT COUNT(*) AS cnt FROM promo_requests WHERE status = 'approved'")

    today_total_usage = count_one("""
        SELECT COUNT(*) AS cnt
        FROM usage_events
        WHERE created_at LIKE ?
    """, (today_prefix + "%",))

    today_search = count_one("""
        SELECT COUNT(*) AS cnt
        FROM usage_events
        WHERE event_type = 'search'
        AND created_at LIKE ?
    """, (today_prefix + "%",))

    today_chat = count_one("""
        SELECT COUNT(*) AS cnt
        FROM usage_events
        WHERE event_type = 'chat'
        AND created_at LIKE ?
    """, (today_prefix + "%",))

    today_game = count_one("""
        SELECT COUNT(*) AS cnt
        FROM usage_events
        WHERE event_type = 'game'
        AND created_at LIKE ?
    """, (today_prefix + "%",))

    usage_by_type_rows = conn.execute("""
        SELECT event_type AS label, COUNT(*) AS count
        FROM usage_events
        WHERE created_at >= ?
        GROUP BY event_type
        ORDER BY count DESC
    """, (week_ago,)).fetchall()

    top_tools_rows = conn.execute("""
        SELECT tool_name AS label, COUNT(*) AS count
        FROM usage_events
        WHERE created_at >= ?
        GROUP BY tool_name
        ORDER BY count DESC
        LIMIT 10
    """, (week_ago,)).fetchall()

    daily_rows = conn.execute("""
        SELECT substr(created_at, 1, 10) AS label, COUNT(*) AS count
        FROM usage_events
        WHERE created_at >= ?
        GROUP BY substr(created_at, 1, 10)
        ORDER BY label ASC
    """, (week_ago,)).fetchall()

    users_rows = conn.execute("""
        SELECT
            id,
            username,
            student_id,
            student_name,
            role,
            account_status,
            status_reason,
            restricted_until,
            created_at
        FROM users
        ORDER BY id DESC
        LIMIT 120
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
                ELSE 2
            END,
            id DESC
        LIMIT 120
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
        LIMIT 60
    """).fetchall()

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
            "title": "24시간 제재 계정 존재",
            "message": f"{restricted_count}개의 계정이 현재 제재 상태입니다."
        })

    if today_chat >= 20:
        important_cards.append({
            "level": "info",
            "title": "챗봇 사용량 증가",
            "message": f"오늘 챗봇 요청이 {today_chat}회 발생했습니다."
        })

    if not important_cards:
        important_cards.append({
            "level": "good",
            "title": "운영 상태 정상",
            "message": "승인 대기, 제재 위험, 과도한 사용량이 크게 보이지 않습니다."
        })

    return {
        "summary": {
            "total_users": total_users,
            "student_count": student_count,
            "teacher_count": teacher_count,
            "active_count": active_count,
            "restricted_count": restricted_count,
            "pending_promos": pending_promos,
            "approved_promos": approved_promos,
            "today_total_usage": today_total_usage,
            "today_search": today_search,
            "today_chat": today_chat,
            "today_game": today_game,
        },
        "important_cards": important_cards,
        "usage_by_type": [gwangle_v4_row_to_dict(row) for row in usage_by_type_rows],
        "top_tools": [gwangle_v4_row_to_dict(row) for row in top_tools_rows],
        "daily_usage": [gwangle_v4_row_to_dict(row) for row in daily_rows],
        "users": [gwangle_v4_row_to_dict(row) for row in users_rows],
        "promo_requests": [gwangle_v4_row_to_dict(row) for row in promo_rows],
        "recent_events": [gwangle_v4_row_to_dict(row) for row in recent_events_rows],
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S")
    }


def gwangle_v4_admin_dashboard_api():
    not_admin = gwangle_v4_admin_json_required()
    if not_admin:
        return not_admin

    return jsonify({
        "success": True,
        "data": gwangle_v4_dashboard_data()
    })


def gwangle_v4_admin_promo_action_api():
    not_admin = gwangle_v4_admin_json_required()
    if not_admin:
        return not_admin

    data = request.get_json(silent=True) or {}

    action = str(data.get("action", "")).strip()
    promo_request_id = data.get("promoRequestId")
    admin_note = str(data.get("adminNote", "")).strip()

    if action not in ["approve", "reject"]:
        return jsonify({
            "success": False,
            "message": "올바르지 않은 홍보 처리 작업입니다."
        }), 400

    try:
        promo_request_id = int(promo_request_id)
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "홍보 신청 ID가 올바르지 않습니다."
        }), 400

    conn = get_db()
    target = conn.execute("""
        SELECT id, image_path, status
        FROM promo_requests
        WHERE id = ?
    """, (promo_request_id,)).fetchone()

    if not target:
        conn.close()
        return jsonify({
            "success": False,
            "message": "홍보 신청을 찾을 수 없습니다."
        }), 404

    if action == "approve":
        if not target["image_path"]:
            conn.close()
            return jsonify({
                "success": False,
                "message": "홍보물 이미지가 없는 신청은 승인할 수 없습니다."
            }), 400

        conn.execute("""
            UPDATE promo_requests
            SET status = 'approved',
                admin_note = ?,
                reviewed_at = ?,
                reviewed_by = ?
            WHERE id = ?
        """, (
            admin_note,
            datetime.datetime.now().isoformat(),
            ADMIN_USERNAME,
            promo_request_id
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": "승인되었습니다. 사용자 메인 화면 팝업에 표시되고, 팝업 클릭 시 홍보물 사진으로 이동합니다.",
            "data": gwangle_v4_dashboard_data()
        })

    if action == "reject":
        conn.execute("""
            DELETE FROM promo_requests
            WHERE id = ?
        """, (promo_request_id,))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": "거절되었습니다. 요청 기록에서 삭제했습니다.",
            "data": gwangle_v4_dashboard_data()
        })


def gwangle_v4_admin_user_action_api():
    not_admin = gwangle_v4_admin_json_required()
    if not_admin:
        return not_admin

    data = request.get_json(silent=True) or {}

    action = str(data.get("action", "")).strip()
    target_user_id = data.get("targetUserId")
    reason = str(data.get("reason", "")).strip()

    if action not in ["restrict", "unrestrict", "delete"]:
        return jsonify({
            "success": False,
            "message": "올바르지 않은 계정 관리 작업입니다."
        }), 400

    try:
        target_user_id = int(target_user_id)
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "대상 계정 ID가 올바르지 않습니다."
        }), 400

    conn = get_db()
    target = conn.execute("""
        SELECT id, username, student_name, role
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
            reason = "관리자에 의해 24시간 이용이 제한되었습니다."

        restricted_until = datetime.datetime.now() + datetime.timedelta(hours=24)

        conn.execute("""
            UPDATE users
            SET account_status = 'restricted',
                status_reason = ?,
                restricted_until = ?
            WHERE id = ?
        """, (
            reason,
            restricted_until.isoformat(),
            target_user_id
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": f"{target['student_name']} 계정을 24시간 제재했습니다.",
            "data": gwangle_v4_dashboard_data()
        })

    if action == "unrestrict":
        conn.execute("""
            UPDATE users
            SET account_status = 'active',
                status_reason = NULL,
                restricted_until = NULL
            WHERE id = ?
        """, (target_user_id,))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": f"{target['student_name']} 계정 제재를 해제했습니다.",
            "data": gwangle_v4_dashboard_data()
        })

    if action == "delete":
        conn.execute("DELETE FROM grade_records WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM mock_exam_records WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM student_record_notes WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM game_scores WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM usage_events WHERE user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM promo_requests WHERE requester_user_id = ?", (target_user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (target_user_id,))

        conn.commit()
        conn.close()

        return jsonify({
            "success": True,
            "message": f"{target['student_name']} 계정을 삭제했습니다.",
            "data": gwangle_v4_dashboard_data()
        })


def gwangle_v4_override_rule(rule_path, methods, endpoint_name, view_func):
    matched = False

    for rule in list(app.url_map.iter_rules()):
        if rule.rule == rule_path:
            app.view_functions[rule.endpoint] = view_func
            matched = True

    if not matched:
        app.add_url_rule(
            rule_path,
            endpoint=endpoint_name,
            view_func=view_func,
            methods=methods
        )


gwangle_v4_override_rule("/admin", ["GET"], "gwangle_v4_admin_page", gwangle_v4_admin_page)
gwangle_v4_override_rule("/promo/request", ["GET", "POST"], "gwangle_v4_promo_request_page", gwangle_v4_promo_request_page)
gwangle_v4_override_rule("/api/promos/approved", ["GET"], "gwangle_v4_approved_promos_api", gwangle_v4_approved_promos_api)
gwangle_v4_override_rule("/promotion/<int:promo_id>", ["GET"], "gwangle_v4_promotion_detail_page", gwangle_v4_promotion_detail_page)
gwangle_v4_override_rule("/api/admin/dashboard", ["GET"], "gwangle_v4_admin_dashboard_api", gwangle_v4_admin_dashboard_api)
gwangle_v4_override_rule("/api/admin/promo-action", ["POST"], "gwangle_v4_admin_promo_action_api", gwangle_v4_admin_promo_action_api)
gwangle_v4_override_rule("/api/admin/user-action", ["POST"], "gwangle_v4_admin_user_action_api", gwangle_v4_admin_user_action_api)

# ==============================
# END GWANGLE ADMIN PROMO V4
# ==============================
'''

ADMIN_HTML = r'''<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>광글 관리자</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">

  <style>
    body {
      background: #ffffff;
      color: #202124;
    }

    .admin-main {
      max-width: 1280px;
      margin: 0 auto;
      padding: 28px 18px 52px;
    }

    .admin-hero {
      border: 1px solid #dadce0;
      border-radius: 28px;
      padding: 26px;
      background: #ffffff;
      box-shadow: 0 2px 14px rgba(60, 64, 67, 0.1);
      margin-bottom: 18px;
    }

    .admin-title {
      margin: 0 0 8px;
      font-size: 34px;
      color: #202124;
    }

    .admin-desc {
      margin: 0;
      color: #5f6368;
      font-size: 15px;
      line-height: 1.6;
    }

    .admin-toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }

    .admin-status {
      color: #5f6368;
      font-size: 13px;
    }

    .admin-btn {
      border: 1px solid #dadce0;
      background: #ffffff;
      color: #3c4043;
      border-radius: 999px;
      padding: 9px 13px;
      cursor: pointer;
      font-size: 13px;
      font-weight: 800;
    }

    .admin-btn.primary {
      background: #1a73e8;
      color: #ffffff;
      border-color: #1a73e8;
    }

    .summary-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 14px;
      margin-bottom: 18px;
    }

    .summary-card {
      border: 1px solid #dadce0;
      border-radius: 22px;
      padding: 18px;
      background: #ffffff;
      box-shadow: 0 2px 10px rgba(60, 64, 67, 0.08);
    }

    .summary-label {
      font-size: 13px;
      color: #5f6368;
      margin-bottom: 8px;
    }

    .summary-value {
      font-size: 30px;
      font-weight: 900;
      color: #1a73e8;
    }

    .summary-sub {
      margin-top: 6px;
      font-size: 12px;
      color: #5f6368;
      line-height: 1.4;
    }

    .admin-section {
      border: 1px solid #dadce0;
      border-radius: 26px;
      background: #ffffff;
      box-shadow: 0 2px 12px rgba(60, 64, 67, 0.08);
      margin-bottom: 18px;
      overflow: hidden;
    }

    .section-head {
      padding: 18px 20px;
      background: #f8fafd;
      border-bottom: 1px solid #eceff1;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }

    .section-title {
      margin: 0;
      font-size: 21px;
      color: #202124;
    }

    .section-desc {
      margin: 4px 0 0;
      color: #5f6368;
      font-size: 13px;
      line-height: 1.5;
    }

    .section-body {
      padding: 18px 20px;
    }

    .chart-grid {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 18px;
    }

    .bar-list {
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .bar-item {
      border: 1px solid #eceff1;
      border-radius: 16px;
      padding: 12px;
      background: #fafafa;
    }

    .bar-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 8px;
      font-size: 13px;
      font-weight: 900;
      color: #202124;
    }

    .bar-track {
      height: 15px;
      border-radius: 999px;
      background: #e8eaed;
      overflow: hidden;
    }

    .bar-fill {
      height: 100%;
      width: 0%;
      border-radius: 999px;
      background: #1a73e8;
      transition: width 0.35s ease;
    }

    .important-list {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
    }

    .important-card {
      border-radius: 20px;
      border: 1px solid #dadce0;
      padding: 15px;
      background: #ffffff;
    }

    .important-card.good {
      background: #e6f4ea;
      border-color: #ceead6;
    }

    .important-card.warning {
      background: #fef7e0;
      border-color: #fde293;
    }

    .important-card.danger {
      background: #fce8e6;
      border-color: #fad2cf;
    }

    .important-card.info {
      background: #e8f0fe;
      border-color: #d2e3fc;
    }

    .important-title {
      font-weight: 900;
      margin-bottom: 6px;
      color: #202124;
    }

    .important-message {
      color: #5f6368;
      font-size: 13px;
      line-height: 1.5;
    }

    .table-wrap {
      overflow-x: auto;
    }

    .admin-table {
      width: 100%;
      border-collapse: collapse;
      min-width: 980px;
    }

    .admin-table th,
    .admin-table td {
      border-bottom: 1px solid #eceff1;
      padding: 12px 10px;
      text-align: left;
      font-size: 13px;
      vertical-align: top;
    }

    .admin-table th {
      color: #5f6368;
      background: #fafafa;
      font-weight: 900;
    }

    .status-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      padding: 5px 9px;
      font-size: 12px;
      font-weight: 900;
      white-space: nowrap;
    }

    .status-active,
    .status-approved {
      background: #e6f4ea;
      color: #137333;
    }

    .status-restricted,
    .status-pending {
      background: #fef7e0;
      color: #b06000;
    }

    .status-deleted,
    .status-rejected {
      background: #fce8e6;
      color: #c5221f;
    }

    .action-row {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }

    .small-btn {
      border: 1px solid #dadce0;
      border-radius: 999px;
      background: #ffffff;
      padding: 7px 10px;
      font-size: 12px;
      cursor: pointer;
      font-weight: 900;
      white-space: nowrap;
    }

    .small-btn.primary {
      background: #1a73e8;
      border-color: #1a73e8;
      color: #ffffff;
    }

    .small-btn.warning {
      background: #fbbc05;
      border-color: #fbbc05;
      color: #202124;
    }

    .small-btn.danger {
      background: #c5221f;
      border-color: #c5221f;
      color: #ffffff;
    }

    .small-btn:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }

    .empty-box {
      border: 1px dashed #c7cdd1;
      border-radius: 18px;
      padding: 22px;
      text-align: center;
      color: #5f6368;
      background: #f8fafd;
    }

    .promo-content {
      max-width: 360px;
      white-space: pre-wrap;
      word-break: keep-all;
      color: #3c4043;
      line-height: 1.5;
    }

    .promo-thumb {
      width: 130px;
      height: 82px;
      object-fit: cover;
      border-radius: 12px;
      border: 1px solid #dadce0;
      background: #f8fafd;
      cursor: pointer;
    }

    .mini-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
    }

    .mini-item {
      border: 1px solid #eceff1;
      border-radius: 16px;
      padding: 12px;
      background: #fafafa;
    }

    .mini-title {
      font-weight: 900;
      color: #202124;
      font-size: 14px;
    }

    .mini-sub {
      color: #5f6368;
      font-size: 12px;
      margin-top: 3px;
      line-height: 1.4;
    }

    .link-text {
      color: #1a73e8;
      text-decoration: none;
      font-weight: 900;
      cursor: pointer;
    }

    @media (max-width: 1100px) {
      .chart-grid {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 900px) {
      .summary-grid,
      .important-list {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }

    @media (max-width: 640px) {
      .summary-grid,
      .important-list {
        grid-template-columns: 1fr;
      }

      .admin-main {
        padding: 20px 12px 42px;
      }

      .admin-title {
        font-size: 28px;
      }
    }
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
        <p class="admin-desc">
          제재는 24시간 사용 금지, 삭제는 계정 삭제, 승인은 사용자 팝업 노출, 거절은 요청 기록 삭제로 처리됩니다.
        </p>
      </section>

      <div class="admin-toolbar">
        <div id="adminStatus" class="admin-status">관리자 데이터를 불러오는 중...</div>
        <button class="admin-btn primary" onclick="loadDashboard()">새로고침</button>
      </div>

      <section class="summary-grid">
        <div class="summary-card">
          <div class="summary-label">전체 계정</div>
          <div id="totalUsers" class="summary-value">-</div>
          <div id="userSub" class="summary-sub">학생 / 선생님</div>
        </div>

        <div class="summary-card">
          <div class="summary-label">오늘 사용 기록</div>
          <div id="todayUsage" class="summary-value">-</div>
          <div id="todayUsageSub" class="summary-sub">검색 / 챗봇 / 게임</div>
        </div>

        <div class="summary-card">
          <div class="summary-label">승인 대기 홍보</div>
          <div id="pendingPromos" class="summary-value">-</div>
          <div id="promoSub" class="summary-sub">승인된 홍보 포함</div>
        </div>

        <div class="summary-card">
          <div class="summary-label">24시간 제재 계정</div>
          <div id="restrictedUsers" class="summary-value">-</div>
          <div id="restrictedSub" class="summary-sub">자동 해제 예정</div>
        </div>
      </section>

      <section class="admin-section">
        <div class="section-head">
          <div>
            <h2 class="section-title">중요 운영 정보</h2>
            <p class="section-desc">관리자가 먼저 확인해야 할 정보를 자동으로 정리합니다.</p>
          </div>
        </div>
        <div class="section-body">
          <div id="importantList" class="important-list"></div>
        </div>
      </section>

      <section class="chart-grid">
        <section class="admin-section">
          <div class="section-head">
            <div>
              <h2 class="section-title">항목별 사용 기록</h2>
              <p class="section-desc">검색, 챗봇, 게임, 도구, 페이지 등 종류별 기록입니다.</p>
            </div>
          </div>
          <div class="section-body">
            <div id="usageByTypeChart" class="bar-list"></div>
          </div>
        </section>

        <section class="admin-section">
          <div class="section-head">
            <div>
              <h2 class="section-title">도구별 사용 기록</h2>
              <p class="section-desc">최근 7일 동안 많이 사용한 도구입니다.</p>
            </div>
          </div>
          <div class="section-body">
            <div id="topToolsChart" class="bar-list"></div>
          </div>
        </section>

        <section class="admin-section">
          <div class="section-head">
            <div>
              <h2 class="section-title">날짜별 사용 기록</h2>
              <p class="section-desc">최근 7일 동안의 날짜별 사용량입니다.</p>
            </div>
          </div>
          <div class="section-body">
            <div id="dailyUsageChart" class="bar-list"></div>
          </div>
        </section>
      </section>

      <section class="admin-section">
        <div class="section-head">
          <div>
            <h2 class="section-title">홍보 신청 관리</h2>
            <p class="section-desc">승인하면 사용자에게 팝업으로 노출됩니다. 거절하면 요청 기록에서 삭제됩니다.</p>
          </div>
        </div>

        <div class="section-body">
          <div class="table-wrap">
            <table class="admin-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>요청자</th>
                  <th>홍보 내용</th>
                  <th>사유</th>
                  <th>홍보물</th>
                  <th>상태</th>
                  <th>요청일</th>
                  <th>관리</th>
                </tr>
              </thead>
              <tbody id="promoTableBody"></tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="admin-section">
        <div class="section-head">
          <div>
            <h2 class="section-title">계정 관리</h2>
            <p class="section-desc">제재는 24시간 사용 금지, 삭제는 계정 DB 삭제입니다.</p>
          </div>
        </div>

        <div class="section-body">
          <div class="table-wrap">
            <table class="admin-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>역할</th>
                  <th>이름</th>
                  <th>아이디</th>
                  <th>학번/교사용 ID</th>
                  <th>상태</th>
                  <th>가입일</th>
                  <th>관리</th>
                </tr>
              </thead>
              <tbody id="userTableBody"></tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="admin-section">
        <div class="section-head">
          <div>
            <h2 class="section-title">최근 상세 로그</h2>
            <p class="section-desc">최근 기능 사용 내역입니다.</p>
          </div>
        </div>

        <div class="section-body">
          <div id="recentEvents" class="mini-list"></div>
        </div>
      </section>
    </main>
  </div>

  <script>
    function safeText(value) {
      if (value === null || value === undefined || value === "") return "-";
      return String(value);
    }

    function roleLabel(role) {
      if (role === "student") return "학생";
      if (role === "teacher") return "선생님";
      if (role === "admin") return "관리자";
      return role || "-";
    }

    function eventTypeLabel(type) {
      const labels = {
        search: "검색",
        chat: "챗봇",
        game: "게임",
        tool: "도구",
        page: "페이지",
        promo: "홍보",
        error: "오류"
      };
      return labels[type] || type || "-";
    }

    function statusBadge(status) {
      const labelMap = {
        active: "정상",
        restricted: "24시간 제재",
        pending: "대기",
        approved: "승인"
      };

      const classMap = {
        active: "status-active",
        restricted: "status-restricted",
        pending: "status-pending",
        approved: "status-approved"
      };

      const span = document.createElement("span");
      span.className = `status-badge ${classMap[status] || "status-active"}`;
      span.textContent = labelMap[status] || status || "-";
      return span;
    }

    function createEmptyRow(colspan, message) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = colspan;
      td.innerHTML = `<div class="empty-box">${message}</div>`;
      tr.appendChild(td);
      return tr;
    }

    function renderBarChart(containerId, rows, emptyMessage, labelFormatter = null) {
      const box = document.getElementById(containerId);
      box.innerHTML = "";

      if (!rows || rows.length === 0) {
        box.innerHTML = `<div class="empty-box">${emptyMessage}</div>`;
        return;
      }

      const maxValue = Math.max(...rows.map(row => Number(row.count) || 0), 1);

      rows.forEach(row => {
        const value = Number(row.count) || 0;
        const percent = Math.max(4, Math.round((value / maxValue) * 100));
        const label = labelFormatter ? labelFormatter(row.label) : row.label;

        const item = document.createElement("div");
        item.className = "bar-item";
        item.innerHTML = `
          <div class="bar-head">
            <span></span>
            <span>${value}회</span>
          </div>
          <div class="bar-track">
            <div class="bar-fill" style="width:${percent}%"></div>
          </div>
        `;

        item.querySelector(".bar-head span").textContent = label;
        box.appendChild(item);
      });
    }

    function renderSummary(summary) {
      document.getElementById("totalUsers").textContent = summary.total_users;
      document.getElementById("userSub").textContent =
        `학생 ${summary.student_count}명 · 선생님 ${summary.teacher_count}명 · 정상 ${summary.active_count}명`;

      document.getElementById("todayUsage").textContent = summary.today_total_usage;
      document.getElementById("todayUsageSub").textContent =
        `검색 ${summary.today_search}회 · 챗봇 ${summary.today_chat}회 · 게임 ${summary.today_game}회`;

      document.getElementById("pendingPromos").textContent = summary.pending_promos;
      document.getElementById("promoSub").textContent =
        `승인된 홍보 ${summary.approved_promos}개`;

      document.getElementById("restrictedUsers").textContent = summary.restricted_count;
      document.getElementById("restrictedSub").textContent = "24시간 후 자동 해제";
    }

    function renderImportant(cards) {
      const box = document.getElementById("importantList");
      box.innerHTML = "";

      if (!cards || cards.length === 0) {
        box.innerHTML = `<div class="empty-box">중요 알림이 없습니다.</div>`;
        return;
      }

      cards.forEach(card => {
        const div = document.createElement("div");
        div.className = `important-card ${card.level || "info"}`;
        div.innerHTML = `
          <div class="important-title"></div>
          <div class="important-message"></div>
        `;
        div.querySelector(".important-title").textContent = card.title;
        div.querySelector(".important-message").textContent = card.message;
        box.appendChild(div);
      });
    }

    function renderPromoRequests(list) {
      const tbody = document.getElementById("promoTableBody");
      tbody.innerHTML = "";

      if (!list || list.length === 0) {
        tbody.appendChild(createEmptyRow(8, "홍보 신청이 없습니다."));
        return;
      }

      list.forEach(item => {
        const tr = document.createElement("tr");

        const tdId = document.createElement("td");
        tdId.textContent = item.id;

        const tdRequester = document.createElement("td");
        tdRequester.textContent = `${safeText(item.requester_name)} (${roleLabel(item.requester_role)})`;

        const tdWhat = document.createElement("td");
        const what = document.createElement("div");
        what.className = "mini-title";
        what.textContent = item.promo_what || "-";

        const imageLink = document.createElement("a");
        imageLink.className = "link-text";
        imageLink.href = item.image_path || "#";
        imageLink.target = "_blank";
        imageLink.textContent = "홍보물 사진 보기";

        tdWhat.appendChild(what);
        tdWhat.appendChild(imageLink);

        const tdReason = document.createElement("td");
        const reason = document.createElement("div");
        reason.className = "promo-content";
        reason.textContent = item.promo_reason || "-";
        tdReason.appendChild(reason);

        const tdImage = document.createElement("td");
        if (item.image_path) {
          const img = document.createElement("img");
          img.className = "promo-thumb";
          img.src = item.image_path;
          img.alt = "홍보물";
          img.onclick = () => window.open(item.image_path, "_blank");
          tdImage.appendChild(img);
        } else {
          tdImage.textContent = "-";
        }

        const tdStatus = document.createElement("td");
        tdStatus.appendChild(statusBadge(item.status));

        const tdDate = document.createElement("td");
        tdDate.textContent = item.created_at ? item.created_at.slice(0, 16).replace("T", " ") : "-";

        const tdAction = document.createElement("td");
        const actions = document.createElement("div");
        actions.className = "action-row";

        if (item.status === "pending") {
          const approveBtn = document.createElement("button");
          approveBtn.className = "small-btn primary";
          approveBtn.textContent = "승인";
          approveBtn.onclick = () => handlePromoAction(item.id, "approve", approveBtn);

          const rejectBtn = document.createElement("button");
          rejectBtn.className = "small-btn danger";
          rejectBtn.textContent = "거절";
          rejectBtn.onclick = () => handlePromoAction(item.id, "reject", rejectBtn);

          actions.appendChild(approveBtn);
          actions.appendChild(rejectBtn);
        } else {
          const doneBtn = document.createElement("button");
          doneBtn.className = "small-btn";
          doneBtn.textContent = "승인 완료";
          doneBtn.disabled = true;
          actions.appendChild(doneBtn);
        }

        tdAction.appendChild(actions);

        [tdId, tdRequester, tdWhat, tdReason, tdImage, tdStatus, tdDate, tdAction].forEach(td => {
          tr.appendChild(td);
        });

        tbody.appendChild(tr);
      });
    }

    function renderUsers(list) {
      const tbody = document.getElementById("userTableBody");
      tbody.innerHTML = "";

      if (!list || list.length === 0) {
        tbody.appendChild(createEmptyRow(8, "회원 계정이 없습니다."));
        return;
      }

      list.forEach(user => {
        const tr = document.createElement("tr");

        [user.id, roleLabel(user.role), user.student_name, user.username, user.student_id].forEach(value => {
          const td = document.createElement("td");
          td.textContent = safeText(value);
          tr.appendChild(td);
        });

        const tdStatus = document.createElement("td");
        tdStatus.appendChild(statusBadge(user.account_status));

        if (user.restricted_until) {
          const until = document.createElement("div");
          until.className = "mini-sub";
          until.textContent = `해제 예정: ${user.restricted_until.slice(0, 16).replace("T", " ")}`;
          tdStatus.appendChild(until);
        }

        if (user.status_reason) {
          const reason = document.createElement("div");
          reason.className = "mini-sub";
          reason.textContent = user.status_reason;
          tdStatus.appendChild(reason);
        }

        tr.appendChild(tdStatus);

        const tdDate = document.createElement("td");
        tdDate.textContent = user.created_at ? user.created_at.slice(0, 16).replace("T", " ") : "-";
        tr.appendChild(tdDate);

        const tdAction = document.createElement("td");
        const actions = document.createElement("div");
        actions.className = "action-row";

        if (user.account_status === "active") {
          const restrictBtn = document.createElement("button");
          restrictBtn.className = "small-btn warning";
          restrictBtn.textContent = "24시간 제재";
          restrictBtn.onclick = () => handleUserAction(user.id, "restrict", restrictBtn);

          const deleteBtn = document.createElement("button");
          deleteBtn.className = "small-btn danger";
          deleteBtn.textContent = "계정 삭제";
          deleteBtn.onclick = () => handleUserAction(user.id, "delete", deleteBtn);

          actions.appendChild(restrictBtn);
          actions.appendChild(deleteBtn);
        } else if (user.account_status === "restricted") {
          const unrestrictBtn = document.createElement("button");
          unrestrictBtn.className = "small-btn primary";
          unrestrictBtn.textContent = "제재 해제";
          unrestrictBtn.onclick = () => handleUserAction(user.id, "unrestrict", unrestrictBtn);

          const deleteBtn = document.createElement("button");
          deleteBtn.className = "small-btn danger";
          deleteBtn.textContent = "계정 삭제";
          deleteBtn.onclick = () => handleUserAction(user.id, "delete", deleteBtn);

          actions.appendChild(unrestrictBtn);
          actions.appendChild(deleteBtn);
        }

        tdAction.appendChild(actions);
        tr.appendChild(tdAction);
        tbody.appendChild(tr);
      });
    }

    function renderRecentEvents(list) {
      const box = document.getElementById("recentEvents");
      box.innerHTML = "";

      if (!list || list.length === 0) {
        box.innerHTML = `<div class="empty-box">최근 사용 기록이 없습니다.</div>`;
        return;
      }

      list.forEach(event => {
        const div = document.createElement("div");
        div.className = "mini-item";
        div.innerHTML = `
          <div class="mini-title"></div>
          <div class="mini-sub"></div>
        `;

        div.querySelector(".mini-title").textContent =
          `${safeText(event.tool_name)} · ${eventTypeLabel(event.event_type)}`;

        div.querySelector(".mini-sub").textContent =
          `${roleLabel(event.user_role)} · ${event.created_at ? event.created_at.slice(0, 19).replace("T", " ") : "-"}`;

        box.appendChild(div);
      });
    }

    async function handlePromoAction(promoId, action, button) {
      let note = "";

      if (action === "approve") {
        const ok = confirm("이 홍보 신청을 승인할까요? 승인되면 사용자 메인 팝업에 표시됩니다.");
        if (!ok) return;
      }

      if (action === "reject") {
        const ok = confirm("이 홍보 신청을 거절하고 요청 기록에서 삭제할까요?");
        if (!ok) return;
      }

      button.disabled = true;
      button.textContent = "처리 중...";

      try {
        const res = await fetch("/api/admin/promo-action", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            promoRequestId: promoId,
            action: action,
            adminNote: note
          })
        });

        const data = await res.json();

        if (!res.ok || !data.success) {
          alert(data.message || "홍보 신청 처리에 실패했습니다.");
          loadDashboard();
          return;
        }

        alert(data.message || "처리되었습니다.");
        renderDashboard(data.data);

      } catch (error) {
        alert("홍보 신청 처리 중 오류가 발생했습니다.");
        loadDashboard();
      }
    }

    async function handleUserAction(userId, action, button) {
      let reason = "";

      if (action === "restrict") {
        reason = prompt("24시간 제재 사유를 입력하세요.", "관리자에 의해 24시간 이용이 제한되었습니다.");
        if (reason === null) return;
      }

      if (action === "delete") {
        const ok = confirm("정말 이 계정을 삭제할까요? 삭제 후 복구하기 어렵습니다.");
        if (!ok) return;
      }

      if (action === "unrestrict") {
        const ok = confirm("이 계정의 제재를 해제할까요?");
        if (!ok) return;
      }

      button.disabled = true;
      button.textContent = "처리 중...";

      try {
        const res = await fetch("/api/admin/user-action", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({
            targetUserId: userId,
            action: action,
            reason: reason
          })
        });

        const data = await res.json();

        if (!res.ok || !data.success) {
          alert(data.message || "계정 처리에 실패했습니다.");
          loadDashboard();
          return;
        }

        alert(data.message || "처리되었습니다.");
        renderDashboard(data.data);

      } catch (error) {
        alert("계정 처리 중 오류가 발생했습니다.");
        loadDashboard();
      }
    }

    function renderDashboard(data) {
      renderSummary(data.summary);
      renderImportant(data.important_cards);

      renderBarChart(
        "usageByTypeChart",
        data.usage_by_type,
        "항목별 사용 기록이 없습니다.",
        eventTypeLabel
      );

      renderBarChart(
        "topToolsChart",
        data.top_tools,
        "도구별 사용 기록이 없습니다."
      );

      renderBarChart(
        "dailyUsageChart",
        data.daily_usage,
        "날짜별 사용 기록이 없습니다."
      );

      renderPromoRequests(data.promo_requests);
      renderUsers(data.users);
      renderRecentEvents(data.recent_events);

      document.getElementById("adminStatus").textContent =
        `마지막 업데이트: ${data.updated_at} · 5초마다 자동 갱신`;
    }

    async function loadDashboard() {
      try {
        const res = await fetch("/api/admin/dashboard");
        const data = await res.json();

        if (!res.ok || !data.success) {
          document.getElementById("adminStatus").textContent =
            data.message || "관리자 데이터를 불러오지 못했습니다.";
          return;
        }

        renderDashboard(data.data);

      } catch (error) {
        document.getElementById("adminStatus").textContent =
          "관리자 데이터를 불러오는 중 오류가 발생했습니다.";
      }
    }

    loadDashboard();
    setInterval(loadDashboard, 5000);
  </script>
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
  from { transform: scale(0.92); opacity: 0; }
  to { transform: scale(1); opacity: 1; }
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

  function openPromoImage(promo) {
    if (promo && promo.image_path) {
      location.href = promo.image_path;
    }
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
          <button id="gwanglePromoPopupDetailBtn" class="promo-popup-btn primary">홍보물 보기</button>
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

      const promo = data.promos[Math.floor(Math.random() * data.promos.length)];

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

      image.onclick = () => openPromoImage(promo);
      title.onclick = () => openPromoImage(promo);
      detailBtn.onclick = () => openPromoImage(promo);
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


def backup(path: Path, backup_dir: Path):
    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / path.name)


def ensure_import(text: str, import_line: str):
    if import_line in text:
        return text

    lines = text.splitlines()
    insert_index = 0

    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            insert_index = i + 1

    lines.insert(insert_index, import_line)
    return "\n".join(lines) + "\n"


def patch_app():
    text = APP_PATH.read_text(encoding="utf-8")

    text = ensure_import(text, "import uuid")

    if "MAX_CONTENT_LENGTH" in text:
        text = re.sub(
            r"MAX_CONTENT_LENGTH\s*=\s*.*",
            "MAX_CONTENT_LENGTH = 25 * 1024 * 1024",
            text,
            count=1
        )

    old_blocks = [
        r"\n# ={10,}\n# GWANGLE ADMIN PROMO V4[\s\S]*?# END GWANGLE ADMIN PROMO V4\n# ={10,}\n",
        r"\n# ={10,}\n# FORCE ADMIN FIX V3[\s\S]*?# END FORCE ADMIN FIX V3\n# ={10,}\n",
    ]

    for pattern in old_blocks:
        text = re.sub(pattern, "\n", text)

    marker = '\nif __name__ == "__main__":'

    if marker in text:
        text = text.replace(marker, "\n" + FORCE_BLOCK + "\n" + marker, 1)
    else:
        text = text.rstrip() + "\n\n" + FORCE_BLOCK + "\n"

    APP_PATH.write_text(text, encoding="utf-8")


def patch_index_shortcut():
    if not INDEX_PATH.exists():
        print("⚠️ templates/index.html을 찾지 못했습니다.")
        return

    text = INDEX_PATH.read_text(encoding="utf-8")

    if 'href="/promo/request"' in text or "href='/promo/request'" in text:
        INDEX_PATH.write_text(text, encoding="utf-8")
        return

    shortcut = '''
          <a href="/promo/request" class="shortcut-item">
            <div class="shortcut-circle">📢</div>
            <div class="shortcut-label">홍보 신청</div>
          </a>
'''

    pattern = re.compile(r'(\s*</div>\s*</section>\s*<section id="resultsArea")', re.IGNORECASE)

    if pattern.search(text):
        text = pattern.sub("\n" + shortcut + r"\1", text, count=1)
    else:
        shortcut_section = text.find("shortcut-section")
        section_end = text.find("</section>", shortcut_section)

        if shortcut_section != -1 and section_end != -1:
            text = text[:section_end] + shortcut + "\n" + text[section_end:]
        else:
            print("⚠️ shortcut-section 위치를 찾지 못했습니다. index.html 즐겨찾기를 직접 확인해주세요.")

    INDEX_PATH.write_text(text, encoding="utf-8")


def write_frontend_files():
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

    ADMIN_PATH.write_text(ADMIN_HTML, encoding="utf-8")
    PROMO_JS_PATH.write_text(PROMO_POPUP_JS, encoding="utf-8")
    PROMO_CSS_PATH.write_text(PROMO_POPUP_CSS, encoding="utf-8")


def main():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BASE_DIR / f"backup_before_admin_promo_v4_{timestamp}"

    for path in [APP_PATH, INDEX_PATH, ADMIN_PATH, PROMO_JS_PATH, PROMO_CSS_PATH]:
        backup(path, backup_dir)

    patch_app()
    patch_index_shortcut()
    write_frontend_files()

    print("✅ 관리자 기능 V4 적용 완료")
    print("✅ 제재 = 24시간 사용 금지")
    print("✅ 삭제 = 계정 DB 삭제")
    print("✅ 승인 = 사용자 팝업 노출 + 팝업 클릭 시 홍보물 사진 이동")
    print("✅ 거절 = 요청 기록에서 삭제")
    print("✅ 항목별/도구별/날짜별 막대그래프 적용")
    print(f"✅ 백업 폴더: {backup_dir}")


if __name__ == "__main__":
    main()