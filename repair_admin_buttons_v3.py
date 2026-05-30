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


FORCE_BLOCK = r'''
# ==============================
# FORCE ADMIN FIX V3
# 관리자 버튼/API 강제 연결 + 홍보 승인/거절 + 계정 제재/삭제 + 막대그래프 통계
# ==============================

PROMO_UPLOAD_DIR = os.path.join("static", "uploads", "promos")
PROMO_MAX_IMAGE_BYTES = 20 * 1024 * 1024
PROMO_ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def gwangle_force_v3_init_db():
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


gwangle_force_v3_init_db()


def gwangle_force_v3_row_to_dict(row):
    if not row:
        return None
    return {key: row[key] for key in row.keys()}


def gwangle_force_v3_is_admin():
    user = get_current_user()
    return bool(user and user["role"] == "admin")


def gwangle_force_v3_admin_json_required():
    if not gwangle_force_v3_is_admin():
        return jsonify({
            "success": False,
            "message": "관리자 로그인 후 이용할 수 있습니다."
        }), 403
    return None


def gwangle_force_v3_admin_page():
    if not gwangle_force_v3_is_admin():
        flash("관리자만 접근할 수 있습니다.")
        return redirect(url_for("login_page"))
    return render_template("admin.html")


def gwangle_force_v3_safe_json_dumps(data):
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        return "{}"


def gwangle_force_v3_log_usage(event_type, tool_name, detail=None):
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
            gwangle_force_v3_safe_json_dumps(detail or {}),
            datetime.datetime.now().isoformat()
        ))
        conn.commit()
        conn.close()
    except Exception:
        pass


@app.after_request
def gwangle_force_v3_usage_logger(response):
    try:
        if response.status_code >= 400:
            return response

        if request.path == "/api/search":
            query = request.args.get("query", "")
            if query:
                gwangle_force_v3_log_usage("search", "검색", {"query": query})

        elif request.path == "/api/chat" and request.method == "POST":
            data = request.get_json(silent=True) or {}
            msg = data.get("message", "")
            gwangle_force_v3_log_usage("chat", "챗봇", {"message_length": len(msg)})

        elif request.path == "/api/meal":
            gwangle_force_v3_log_usage("tool", "급식", {"mode": request.args.get("mode", "")})

        elif request.path == "/api/timetable":
            gwangle_force_v3_log_usage("tool", "시간표", {
                "grade": request.args.get("grade", ""),
                "class": request.args.get("class", "")
            })

        elif request.path == "/api/game/start":
            data = request.get_json(silent=True) or {}
            gwangle_force_v3_log_usage("game", "미니게임 시작", {"game_type": data.get("gameType", "")})

        elif request.path == "/api/game/submit":
            data = request.get_json(silent=True) or {}
            gwangle_force_v3_log_usage("game", "미니게임 점수 제출", {
                "game_type": data.get("gameType", ""),
                "score": data.get("score", 0)
            })

        elif request.path == "/teachers":
            gwangle_force_v3_log_usage("page", "선생님 목록", {})

        elif request.path == "/game":
            gwangle_force_v3_log_usage("page", "미니게임 페이지", {})

        elif request.path == "/profile":
            gwangle_force_v3_log_usage("page", "프로필", {})

        elif request.path.startswith("/promotion/"):
            gwangle_force_v3_log_usage("promo", "홍보 상세", {"path": request.path})

    except Exception:
        pass

    return response


def gwangle_force_v3_dashboard_data():
    conn = get_db()

    today_prefix = datetime.date.today().isoformat()
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()

    def count_one(sql, params=()):
        return conn.execute(sql, params).fetchone()["cnt"]

    total_users = count_one("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE account_status != 'deleted'
    """)

    student_count = count_one("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE role = 'student'
        AND account_status != 'deleted'
    """)

    teacher_count = count_one("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE role = 'teacher'
        AND account_status != 'deleted'
    """)

    active_count = count_one("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE account_status = 'active'
    """)

    restricted_count = count_one("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE account_status = 'restricted'
    """)

    deleted_count = count_one("""
        SELECT COUNT(*) AS cnt
        FROM users
        WHERE account_status = 'deleted'
    """)

    pending_promos = count_one("""
        SELECT COUNT(*) AS cnt
        FROM promo_requests
        WHERE status = 'pending'
    """)

    approved_promos = count_one("""
        SELECT COUNT(*) AS cnt
        FROM promo_requests
        WHERE status = 'approved'
    """)

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
        LIMIT 100
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
        LIMIT 100
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
        LIMIT 50
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
            "title": "제재 계정 존재",
            "message": f"{restricted_count}개의 계정이 제재 상태입니다."
        })

    if today_chat >= 20:
        important_cards.append({
            "level": "info",
            "title": "챗봇 사용량 증가",
            "message": f"오늘 챗봇 요청이 {today_chat}회 발생했습니다. API 사용량을 확인하세요."
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
            "deleted_count": deleted_count,
            "pending_promos": pending_promos,
            "approved_promos": approved_promos,
            "today_total_usage": today_total_usage,
            "today_search": today_search,
            "today_chat": today_chat,
            "today_game": today_game,
        },
        "important_cards": important_cards,
        "usage_by_type": [gwangle_force_v3_row_to_dict(row) for row in usage_by_type_rows],
        "top_tools": [gwangle_force_v3_row_to_dict(row) for row in top_tools_rows],
        "daily_usage": [gwangle_force_v3_row_to_dict(row) for row in daily_rows],
        "users": [gwangle_force_v3_row_to_dict(row) for row in recent_users_rows],
        "promo_requests": [gwangle_force_v3_row_to_dict(row) for row in promo_rows],
        "recent_events": [gwangle_force_v3_row_to_dict(row) for row in recent_events_rows],
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def gwangle_force_v3_admin_dashboard_api():
    not_admin = gwangle_force_v3_admin_json_required()
    if not_admin:
        return not_admin

    return jsonify({
        "success": True,
        "data": gwangle_force_v3_dashboard_data()
    })


def gwangle_force_v3_admin_promo_action_api():
    not_admin = gwangle_force_v3_admin_json_required()
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

    status = "approved" if action == "approve" else "rejected"

    conn = get_db()
    target = conn.execute("""
        SELECT id, COALESCE(promo_what, title) AS promo_what, image_path, status
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

    message = "홍보 신청을 승인했습니다." if status == "approved" else "홍보 신청을 거절했습니다."

    return jsonify({
        "success": True,
        "message": message,
        "data": gwangle_force_v3_dashboard_data()
    })


def gwangle_force_v3_admin_user_action_api():
    not_admin = gwangle_force_v3_admin_json_required()
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

    return jsonify({
        "success": True,
        "message": message,
        "data": gwangle_force_v3_dashboard_data()
    })


def gwangle_force_v3_override_rule(rule_path, methods, endpoint_name, view_func):
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


gwangle_force_v3_override_rule(
    "/admin",
    ["GET"],
    "gwangle_force_v3_admin_page",
    gwangle_force_v3_admin_page
)

gwangle_force_v3_override_rule(
    "/api/admin/dashboard",
    ["GET"],
    "gwangle_force_v3_admin_dashboard_api",
    gwangle_force_v3_admin_dashboard_api
)

gwangle_force_v3_override_rule(
    "/api/admin/promo-action",
    ["POST"],
    "gwangle_force_v3_admin_promo_action_api",
    gwangle_force_v3_admin_promo_action_api
)

gwangle_force_v3_override_rule(
    "/api/admin/user-action",
    ["POST"],
    "gwangle_force_v3_admin_user_action_api",
    gwangle_force_v3_admin_user_action_api
)
# ==============================
# END FORCE ADMIN FIX V3
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
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
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
          홍보 승인/거절, 계정 제재/삭제, 사용 기록 통계를 관리합니다.
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
          <div class="summary-label">제재 / 삭제 계정</div>
          <div id="restrictedUsers" class="summary-value">-</div>
          <div id="restrictedSub" class="summary-sub">운영 주의 필요</div>
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
              <p class="section-desc">검색, 챗봇, 게임, 페이지 등 이벤트 종류별 기록입니다.</p>
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
            <p class="section-desc">승인 버튼을 누르면 메인 팝업과 홍보 상세 페이지에 표시됩니다.</p>
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
            <p class="section-desc">학생/선생님 계정을 제재, 제재 해제, 삭제 처리할 수 있습니다.</p>
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
        restricted: "제재",
        deleted: "삭제",
        pending: "대기",
        approved: "승인",
        rejected: "거절"
      };

      const classMap = {
        active: "status-active",
        restricted: "status-restricted",
        deleted: "status-deleted",
        pending: "status-pending",
        approved: "status-approved",
        rejected: "status-rejected"
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

      document.getElementById("restrictedUsers").textContent =
        summary.restricted_count + summary.deleted_count;
      document.getElementById("restrictedSub").textContent =
        `제재 ${summary.restricted_count}명 · 삭제 ${summary.deleted_count}명`;
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

        const detailLink = document.createElement("a");
        detailLink.className = "link-text";
        detailLink.href = `/promotion/${item.id}`;
        detailLink.target = "_blank";
        detailLink.textContent = "상세 보기";

        tdWhat.appendChild(what);
        tdWhat.appendChild(detailLink);

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
          img.onclick = () => window.open(`/promotion/${item.id}`, "_blank");
          tdImage.appendChild(img);
        } else {
          tdImage.textContent = "-";
        }

        const tdStatus = document.createElement("td");
        tdStatus.appendChild(statusBadge(item.status));

        if (item.admin_note) {
          const note = document.createElement("div");
          note.className = "mini-sub";
          note.textContent = item.admin_note;
          tdStatus.appendChild(note);
        }

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
          doneBtn.textContent = "처리 완료";
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
          restrictBtn.textContent = "제재";
          restrictBtn.onclick = () => handleUserAction(user.id, "restrict", restrictBtn);

          const deleteBtn = document.createElement("button");
          deleteBtn.className = "small-btn danger";
          deleteBtn.textContent = "삭제";
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
          deleteBtn.textContent = "삭제";
          deleteBtn.onclick = () => handleUserAction(user.id, "delete", deleteBtn);

          actions.appendChild(unrestrictBtn);
          actions.appendChild(deleteBtn);
        } else {
          const deletedBtn = document.createElement("button");
          deletedBtn.className = "small-btn";
          deletedBtn.textContent = "삭제됨";
          deletedBtn.disabled = true;
          actions.appendChild(deletedBtn);
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
          <div>
            <div class="mini-title"></div>
            <div class="mini-sub"></div>
          </div>
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
        note = prompt("승인 메모를 입력하세요. 비워도 됩니다.", "");
        if (note === null) return;
      }

      if (action === "reject") {
        note = prompt("거절 사유를 입력하세요.", "홍보 기준에 맞지 않아 거절되었습니다.");
        if (note === null) return;
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
          button.disabled = false;
          button.textContent = action === "approve" ? "승인" : "거절";
          return;
        }

        alert(data.message || "처리되었습니다.");
        renderDashboard(data.data);

      } catch (error) {
        alert("홍보 신청 처리 중 오류가 발생했습니다. Error log를 확인해주세요.");
        button.disabled = false;
        button.textContent = action === "approve" ? "승인" : "거절";
      }
    }

    async function handleUserAction(userId, action, button) {
      let reason = "";

      if (action === "restrict") {
        reason = prompt("제재 사유를 입력하세요.", "관리자에 의해 계정 이용이 제한되었습니다.");
        if (reason === null) return;
      }

      if (action === "delete") {
        const ok = confirm("정말 이 계정을 삭제 처리할까요? 삭제된 계정은 로그인할 수 없습니다.");
        if (!ok) return;

        reason = prompt("삭제 사유를 입력하세요.", "관리자에 의해 계정이 삭제 처리되었습니다.");
        if (reason === null) return;
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
        alert("계정 처리 중 오류가 발생했습니다. Error log를 확인해주세요.");
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

    text = re.sub(
        r"\n# ={10,}\n# FORCE ADMIN FIX V3[\s\S]*?# END FORCE ADMIN FIX V3\n# ={10,}\n",
        "\n",
        text
    )

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

    target = '<section id="resultsArea"'
    pos = text.find(target)

    if pos != -1:
        text = text[:pos] + shortcut + "\n      " + text[pos:]
    else:
        grid_end = text.find('</section>', text.find('shortcut-section'))
        if grid_end != -1:
            text = text[:grid_end] + shortcut + "\n" + text[grid_end:]

    INDEX_PATH.write_text(text, encoding="utf-8")


def write_admin_html():
    TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    ADMIN_PATH.write_text(ADMIN_HTML, encoding="utf-8")


def main():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BASE_DIR / f"backup_before_admin_fix_v3_{timestamp}"

    backup(APP_PATH, backup_dir)
    backup(INDEX_PATH, backup_dir)
    backup(ADMIN_PATH, backup_dir)

    patch_app()
    patch_index_shortcut()
    write_admin_html()

    print("✅ 관리자 버튼/API 강제 수정 완료")
    print("✅ 홍보 승인/거절 버튼 연결 완료")
    print("✅ 계정 제재/삭제 버튼 연결 완료")
    print("✅ 항목별/도구별/날짜별 사용 기록 막대그래프 구현 완료")
    print("✅ 메인 즐겨찾기 홍보 신청 버튼 확인/추가 완료")
    print(f"✅ 백업 폴더: {backup_dir}")


if __name__ == "__main__":
    main()