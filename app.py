from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
import json
import os
import re
import sqlite3
import requests
import datetime
import time
import threading
import uuid
from collections import defaultdict, deque
from functools import wraps
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "gwangle-dev-secret-key")

SERVICE_KEY = os.getenv("EDU_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "godtaeyuen")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "rhkdrmf1234")

BASE_URL = "https://open.neis.go.kr/hub/"
DATABASE = "gwangle.db"

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OpenAI and OPENAI_API_KEY else None


MAX_CONTENT_LENGTH = 128 * 1024

RATE_LIMITS = {
    "/api/chat": (12, 60),
    "/api/search": (90, 60),
    "/api/suggest": (90, 60),
    "/api/game/start": (15, 60),
    "/api/game/submit": (15, 60),
    "/api/game/leaderboard": (120, 60),
    "/login": (20, 300),
    "/signup": (20, 300),
    "/signup/step2": (20, 300),
    "/profile": (30, 300),
    "/admin": (60, 300),
}

DEFAULT_POST_LIMIT = (60, 60)

FIELD_LIMITS = {
    "message": 800,
    "query": 100,

    "loginRole": 20,
    "signupRole": 20,

    "username": 40,
    "password": 100,
    "passwordConfirm": 100,
    "currentPassword": 100,
    "newPassword": 100,
    "newPasswordConfirm": 100,

    "studentName": 20,
    "studentId": 10,
    "homeroomTeacherId": 10,

    "teacherName": 20,
    "teacherSubject": 40,
    "teacherPosition": 40,

    "semester": 30,
    "subject": 30,
    "score": 20,
    "gradeLevel": 20,
    "note": 300,

    "examName": 40,
    "examDate": 20,
    "koreanGrade": 20,
    "mathGrade": 20,
    "englishGrade": 20,
    "inquiryGrade": 20,
    "mockNote": 300,

    "careerGoal": 100,
    "targetMajor": 100,
    "targetUniversities": 200,
    "studentRecordSummary": 2000,
    "activitiesSummary": 1500,
    "strengths": 1000,
    "concerns": 1000,

    "gameId": 80,
    "gameType": 30,

    "promoWhat": 80,
    "promoReason": 1000,
    "adminNote": 300,
}

DEFAULT_FIELD_LIMIT = 300

_request_log = defaultdict(deque)
_lock = threading.Lock()

app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH


def security_error_response(message, status_code):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "message": message,
            "reply": message
        }), status_code

    return message, status_code


def client_key():
    ip = request.remote_addr or "unknown"
    user_id = session.get("user_id", "guest")
    user_role = session.get("user_role", "guest")
    return f"{ip}:{user_id}:{user_role}:{request.method}:{request.path}"


def is_rate_limited(key, limit, window_seconds):
    now = time.time()

    with _lock:
        q = _request_log[key]

        while q and now - q[0] > window_seconds:
            q.popleft()

        if len(q) >= limit:
            return True

        q.append(now)
        return False


def has_bad_control_chars(text):
    for ch in text:
        code = ord(ch)
        if code < 32 and ch not in ["\n", "\r", "\t"]:
            return True
    return False


def has_extreme_repetition(text):
    if len(text) < 200:
        return False

    same_count = 1
    prev = ""

    for ch in text:
        if ch == prev:
            same_count += 1
            if same_count >= 120:
                return True
        else:
            same_count = 1
            prev = ch

    return False


def validate_text(field_name, value):
    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    limit = FIELD_LIMITS.get(field_name, DEFAULT_FIELD_LIMIT)

    if len(value) > limit:
        return f"'{field_name}' 입력이 너무 깁니다. 최대 {limit}자까지 입력할 수 있습니다."

    if has_bad_control_chars(value):
        return f"'{field_name}' 입력에 허용되지 않는 문자가 포함되어 있습니다."

    if has_extreme_repetition(value):
        return f"'{field_name}' 입력에 비정상적으로 반복되는 문자가 너무 많습니다."

    return None


def walk_json(data, parent_key=""):
    if isinstance(data, dict):
        for key, value in data.items():
            error = walk_json(value, str(key))
            if error:
                return error

    elif isinstance(data, list):
        if len(data) > 50:
            return "한 번에 너무 많은 데이터를 보낼 수 없습니다."

        for item in data:
            error = walk_json(item, parent_key)
            if error:
                return error

    elif isinstance(data, str):
        return validate_text(parent_key, data)

    return None


def validate_request_inputs():
    for key in request.args:
        for value in request.args.getlist(key):
            error = validate_text(key, value)
            if error:
                return error

    for key in request.form:
        values = request.form.getlist(key)

        if len(values) > 20:
            return f"'{key}' 값이 너무 많이 전송되었습니다."

        for value in values:
            error = validate_text(key, value)
            if error:
                return error

    if request.is_json:
        data = request.get_json(silent=True)

        if data is None:
            return "JSON 요청 형식이 올바르지 않습니다."

        error = walk_json(data)
        if error:
            return error

    return None


@app.before_request
def security_guard():
    content_length = request.content_length

    if content_length is not None and content_length > MAX_CONTENT_LENGTH:
        return security_error_response("요청 데이터가 너무 큽니다.", 413)

    if request.method in ["POST", "PUT", "PATCH"]:
        error = validate_request_inputs()
        if error:
            return security_error_response(error, 400)

    if request.path in RATE_LIMITS:
        limit, window = RATE_LIMITS[request.path]
    elif request.method in ["POST", "PUT", "PATCH"]:
        limit, window = DEFAULT_POST_LIMIT
    else:
        return None

    key = client_key()

    if is_rate_limited(key, limit, window):
        return security_error_response("요청이 너무 많습니다. 잠시 후 다시 시도해주세요.", 429)

    return None


@app.errorhandler(413)
def too_large(error):
    return security_error_response("요청 데이터가 너무 큽니다.", 413)


@app.after_request
def inject_global_loading_script(response):
    try:
        content_type = response.headers.get("Content-Type", "")

        if "text/html" not in content_type:
            return response

        html = response.get_data(as_text=True)
        script_tag = '<script src="/static/loading.js"></script>'

        if script_tag in html:
            return response

        if "</body>" not in html.lower():
            return response

        html = re.sub(
            r"</body\s*>",
            script_tag + "\n</body>",
            html,
            count=1,
            flags=re.IGNORECASE
        )

        response.set_data(html)
        response.headers["Content-Length"] = str(len(response.get_data()))

    except Exception:
        return response

    return response


def load_json_file(path, default):
    if not os.path.exists(path):
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


facilities = load_json_file("data/facilities.json", [])
academic_calendar = load_json_file("data/academic_calendar.json", [])


def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def column_exists(conn, table_name, column_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row["name"] == column_name for row in rows)


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS teachers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            desc TEXT NOT NULL,
            keywords_json TEXT NOT NULL,
            tags_json TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            student_id TEXT NOT NULL UNIQUE,
            student_name TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    if not column_exists(conn, "users", "homeroom_teacher_id"):
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN homeroom_teacher_id INTEGER
        """)

    if not column_exists(conn, "users", "role"):
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN role TEXT NOT NULL DEFAULT 'student'
        """)

    if not column_exists(conn, "users", "teacher_subject"):
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN teacher_subject TEXT
        """)

    if not column_exists(conn, "users", "teacher_position"):
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN teacher_position TEXT
        """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS grade_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            semester TEXT NOT NULL,
            subject TEXT NOT NULL,
            score TEXT,
            grade_level TEXT,
            note TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS mock_exam_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            exam_name TEXT NOT NULL,
            exam_date TEXT,
            korean_grade TEXT,
            math_grade TEXT,
            english_grade TEXT,
            inquiry_grade TEXT,
            note TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS student_record_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            career_goal TEXT,
            target_major TEXT,
            target_universities TEXT,
            student_record_summary TEXT,
            activities_summary TEXT,
            strengths TEXT,
            concerns TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS game_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            student_name TEXT NOT NULL,
            student_id TEXT NOT NULL,
            game_type TEXT NOT NULL DEFAULT 'reaction',
            game_name TEXT NOT NULL DEFAULT '반응속도 챌린지',
            score INTEGER NOT NULL,
            hits INTEGER NOT NULL,
            misses INTEGER NOT NULL,
            combo_max INTEGER NOT NULL,
            accuracy REAL NOT NULL,
            duration_ms INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    if not column_exists(conn, "game_scores", "game_type"):
        cur.execute("ALTER TABLE game_scores ADD COLUMN game_type TEXT NOT NULL DEFAULT 'reaction'")

    if not column_exists(conn, "game_scores", "game_name"):
        cur.execute("ALTER TABLE game_scores ADD COLUMN game_name TEXT NOT NULL DEFAULT '반응속도 챌린지'")

    conn.commit()
    conn.close()


def seed_teachers_from_json():
    conn = get_db()
    count_row = conn.execute("SELECT COUNT(*) AS cnt FROM teachers").fetchone()

    if count_row["cnt"] > 0:
        conn.close()
        return

    if not os.path.exists("data/teachers.json"):
        conn.close()
        return

    with open("data/teachers.json", "r", encoding="utf-8") as f:
        teacher_data = json.load(f)

    for teacher in teacher_data:
        conn.execute("""
            INSERT INTO teachers (type, title, desc, keywords_json, tags_json)
            VALUES (?, ?, ?, ?, ?)
        """, (
            teacher.get("type", "선생님 정보"),
            teacher.get("title", ""),
            teacher.get("desc", ""),
            json.dumps(teacher.get("keywords", []), ensure_ascii=False),
            json.dumps(teacher.get("tags", []), ensure_ascii=False)
        ))

    conn.commit()
    conn.close()


init_db()
seed_teachers_from_json()


def get_all_teachers():
    conn = get_db()
    rows = conn.execute("""
        SELECT id, type, title, desc, keywords_json, tags_json
        FROM teachers
        ORDER BY title ASC
    """).fetchall()
    conn.close()

    teachers = []
    for row in rows:
        teachers.append({
            "id": row["id"],
            "type": row["type"],
            "title": row["title"],
            "desc": row["desc"],
            "keywords": json.loads(row["keywords_json"]) if row["keywords_json"] else [],
            "tags": json.loads(row["tags_json"]) if row["tags_json"] else [],
        })

    return teachers


def get_search_data():
    return get_all_teachers() + facilities


def teacher_exists(teacher_id):
    try:
        teacher_id = int(teacher_id)
    except ValueError:
        return False

    conn = get_db()
    row = conn.execute("SELECT id FROM teachers WHERE id = ?", (teacher_id,)).fetchone()
    conn.close()

    return row is not None


def get_grade_records(user_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, semester, subject, score, grade_level, note, created_at
        FROM grade_records
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,)).fetchall()
    conn.close()
    return rows


def get_mock_exam_records(user_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, exam_name, exam_date, korean_grade, math_grade, english_grade,
               inquiry_grade, note, created_at
        FROM mock_exam_records
        WHERE user_id = ?
        ORDER BY id DESC
    """, (user_id,)).fetchall()
    conn.close()
    return rows


def get_student_record_note(user_id):
    conn = get_db()
    row = conn.execute("""
        SELECT id, user_id, career_goal, target_major, target_universities,
               student_record_summary, activities_summary, strengths, concerns, updated_at
        FROM student_record_notes
        WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()

    if row:
        return row

    return {
        "career_goal": "",
        "target_major": "",
        "target_universities": "",
        "student_record_summary": "",
        "activities_summary": "",
        "strengths": "",
        "concerns": ""
    }


def build_blob(item):
    fields = [item.get("title", ""), item.get("desc", "")]
    fields += item.get("keywords", [])
    fields += item.get("tags", [])
    return " ".join(fields).lower()


def is_match(query, item):
    blob = build_blob(item)
    query = query.lower().strip()

    if not query:
        return False

    if query in blob:
        return True

    query_tokens = query.split()
    if all(token in blob for token in query_tokens):
        return True

    return False


def extract_calendar_month(query):
    query = query.strip().lower()

    month_match = re.search(r"(\d{1,2})\s*월", query)
    if month_match:
        month = int(month_match.group(1))
        if 1 <= month <= 12:
            return month

    if "이번 달" in query or "이번달" in query:
        return datetime.date.today().month

    if "다음 달" in query or "다음달" in query:
        next_month = datetime.date.today().month + 1
        if next_month == 13:
            next_month = 1
        return next_month

    return None


def get_calendar_events_by_month(month):
    for item in academic_calendar:
        if item.get("month") == month:
            return item
    return None


def get_all_calendar_events():
    events = []
    for month_item in academic_calendar:
        for event in month_item.get("events", []):
            events.append({
                "year": month_item.get("year"),
                "month": month_item.get("month"),
                "date": event.get("date"),
                "title": event.get("title")
            })
    return events


def calendar_date_sort_key(event):
    date_text = str(event.get("date", ""))
    numbers = re.findall(r"\d+", date_text)

    if len(numbers) >= 2:
        return int(numbers[0]), int(numbers[1])

    if len(numbers) == 1:
        return int(numbers[0]), 0

    return 99, 99


def format_calendar_events_text(month):
    calendar_item = get_calendar_events_by_month(month)

    if not calendar_item:
        return f"{month}월 학사일정 정보가 없습니다."

    events = calendar_item.get("events", [])
    if not events:
        return f"{month}월 학사일정이 없습니다."

    events = sorted(events, key=calendar_date_sort_key)

    lines = [f"{month}월 학사일정"]
    for event in events:
        lines.append(f"- {event['date']}: {event['title']}")

    return "\n".join(lines)


def format_all_calendar_events_text():
    events = get_all_calendar_events()

    if not events:
        return "등록된 학사일정 정보가 없습니다."

    grouped = {}
    for event in events:
        month = event.get("month")
        if month not in grouped:
            grouped[month] = []
        grouped[month].append(event)

    month_order = [5, 6, 7, 8, 9, 10, 11, 12]

    lines = ["전체 학사일정"]

    for month in month_order:
        if month not in grouped:
            continue

        lines.append(f"\n[{month}월]")

        month_events = sorted(grouped[month], key=calendar_date_sort_key)

        for event in month_events:
            lines.append(f"- {event['date']}: {event['title']}")

    return "\n".join(lines)


def is_all_calendar_query(query):
    query = query.strip().lower()

    exact_queries = [
        "학사일정",
        "전체 학사일정",
        "학사일정 전체",
        "학교 일정",
        "전체 일정",
        "연간 학사일정",
        "모든 학사일정",
        "학사일정 알려줘",
        "전체 학사일정 알려줘"
    ]

    if query in exact_queries:
        return True

    if ("학사일정" in query or "일정" in query) and any(
        word in query for word in ["전체", "전부", "모든", "연간", "다", "한번에"]
    ):
        return True

    return False


def search_calendar_events_by_keyword(query):
    query = query.lower().strip()

    remove_words = [
        "언제", "알려줘", "뭐야", "뭐 있어", "일정", "학사일정",
        "학사", "행사", "학교", "광명고", "있어", "있나요", "인가요"
    ]

    cleaned = query
    for word in remove_words:
        cleaned = cleaned.replace(word, " ")

    tokens = [t.strip() for t in cleaned.split() if len(t.strip()) >= 2]

    if not tokens:
        return []

    matched = []
    for event in get_all_calendar_events():
        title = event["title"].lower()
        date = event["date"]

        if any(token in title or token in date for token in tokens):
            matched.append(event)

    matched = sorted(matched, key=lambda event: (event.get("month", 99), calendar_date_sort_key(event)))
    return matched


def format_calendar_keyword_result(events):
    if not events:
        return None

    lines = ["관련 학사일정"]
    for event in events:
        lines.append(f"- {event['date']}: {event['title']}")

    return "\n".join(lines)


def is_calendar_query(query):
    calendar_keywords = [
        "일정", "학사", "학사일정", "행사", "시험", "지필", "방학", "개학",
        "수능", "전국연합", "전국연합학력평가", "추석", "대체공휴일",
        "재량휴업일", "체육대회", "체험학습", "졸업식", "종업식"
    ]
    return any(keyword in query for keyword in calendar_keywords)


def build_calendar_search_result(query):
    query_lower = query.lower().strip()

    if not is_calendar_query(query_lower):
        return None

    if is_all_calendar_query(query_lower):
        return {
            "type": "학사일정",
            "title": "전체 학사일정",
            "desc": format_all_calendar_events_text(),
            "tags": ["학사일정", "전체", "광명고"]
        }

    month = extract_calendar_month(query_lower)
    if month:
        return {
            "type": "학사일정",
            "title": f"{month}월 학사일정",
            "desc": format_calendar_events_text(month),
            "tags": ["학사일정", f"{month}월", "광명고"]
        }

    matched_events = search_calendar_events_by_keyword(query_lower)
    if matched_events:
        desc = "\n".join([f"{event['date']}: {event['title']}" for event in matched_events])
        return {
            "type": "학사일정",
            "title": "관련 학사일정",
            "desc": desc,
            "tags": ["학사일정", "광명고"]
        }

    return {
        "type": "학사일정",
        "title": "전체 학사일정",
        "desc": format_all_calendar_events_text(),
        "tags": ["학사일정", "전체", "광명고"]
    }


def clean_menu_list(raw_menu):
    menu_list = []

    for item in raw_menu.split("<br/>"):
        cleaned = item.replace("#", "").strip()

        while cleaned and (cleaned[-1].isdigit() or cleaned[-1] == "."):
            cleaned = cleaned[:-1].strip()

        if cleaned:
            menu_list.append(cleaned)

    return menu_list


def get_school_info(school_name, office_education):
    if not SERVICE_KEY:
        return None

    params = {
        "KEY": SERVICE_KEY,
        "Type": "json",
        "SCHUL_NM": school_name
    }

    response = requests.get(BASE_URL + "schoolInfo", params=params, timeout=15)
    school_data = response.json()

    if "schoolInfo" not in school_data:
        return None

    school_rows = school_data["schoolInfo"][1]["row"]

    for schoolinfo in school_rows:
        if schoolinfo["ATPT_OFCDC_SC_NM"] == office_education:
            return {
                "ATPT_OFCDC_SC_CODE": schoolinfo["ATPT_OFCDC_SC_CODE"],
                "SD_SCHUL_CODE": schoolinfo["SD_SCHUL_CODE"]
            }

    return None


def get_menus_by_day(school_name, office_education, day):
    school_info = get_school_info(school_name, office_education)
    if not school_info:
        return []

    params = {
        "KEY": SERVICE_KEY,
        "Type": "json",
        "ATPT_OFCDC_SC_CODE": school_info["ATPT_OFCDC_SC_CODE"],
        "SD_SCHUL_CODE": school_info["SD_SCHUL_CODE"],
        "MLSV_YMD": day
    }

    response = requests.get(BASE_URL + "mealServiceDietInfo", params=params, timeout=15)
    meal_data = response.json()

    if "mealServiceDietInfo" not in meal_data:
        return []

    raw_menu = meal_data["mealServiceDietInfo"][1]["row"][0]["DDISH_NM"]
    return clean_menu_list(raw_menu)


def get_week_dates(offset_weeks=0):
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(weeks=offset_weeks)
    return [monday + datetime.timedelta(days=i) for i in range(5)]


def get_week_meals(school_name, office_education, offset_weeks=0):
    week_days = get_week_dates(offset_weeks)
    result = []
    weekday_names = ["월", "화", "수", "목", "금", "토", "일"]

    for day in week_days:
        day_str = day.strftime("%Y%m%d")
        menus = get_menus_by_day(school_name, office_education, day_str)

        result.append({
            "date": day.strftime("%Y-%m-%d"),
            "weekday": weekday_names[day.weekday()],
            "menus": menus
        })

    return result


def is_weekend():
    return datetime.date.today().weekday() in [5, 6]


def extract_grade_class(query):
    patterns = [
        r"(\d)\s*학년\s*(\d+)\s*반\s*시간표",
        r"(\d)\s*학년\s*(\d+)\s*반",
        r"(\d)\s*-\s*(\d+)\s*시간표",
        r"(\d)\s*-\s*(\d+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, query)
        if match:
            return match.group(1), match.group(2)

    return None, None


def parse_student_id(student_id):
    student_id = re.sub(r"\D", "", student_id)

    if len(student_id) == 4:
        grade = student_id[0]
        class_nm = student_id[1]
        number = student_id[2:]
        return grade, class_nm, number

    if len(student_id) == 5:
        grade = student_id[0]
        class_nm = str(int(student_id[1:3]))
        number = student_id[3:]
        return grade, class_nm, number

    return None, None, None


def get_current_semester():
    month = datetime.date.today().month
    return "1" if month <= 7 else "2"


def fetch_timetable_day(school_info, ay, sem, grade, class_nm, ymd):
    params = {
        "KEY": SERVICE_KEY,
        "Type": "json",
        "pIndex": 1,
        "pSize": 100,
        "ATPT_OFCDC_SC_CODE": school_info["ATPT_OFCDC_SC_CODE"],
        "SD_SCHUL_CODE": school_info["SD_SCHUL_CODE"],
        "AY": ay,
        "SEM": sem,
        "GRADE": str(grade),
        "CLASS_NM": str(class_nm),
        "ALL_TI_YMD": ymd
    }

    response = requests.get(BASE_URL + "hisTimetable", params=params, timeout=15)
    data = response.json()

    if "hisTimetable" not in data:
        return []

    return data["hisTimetable"][1]["row"]


def pick_first(row, keys, default=""):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def normalize_timetable_rows(rows):
    normalized = []

    for row in rows:
        ymd = pick_first(row, ["ALL_TI_YMD", "TI_FROM_YMD", "DATE"], "")
        period = pick_first(row, ["PERIO", "TIME", "PERIOD"], "")
        subject = pick_first(row, ["ITRT_CNTNT", "SUBJECT", "SBTR_DD_SC_NM"], "")
        room = pick_first(row, ["CLRM_NM", "ROOM", "CLASSROOM"], "")

        normalized.append({
            "date": str(ymd),
            "period": str(period),
            "subject": str(subject),
            "room": str(room)
        })

    return normalized


def weekday_kor(yyyymmdd):
    d = datetime.datetime.strptime(yyyymmdd, "%Y%m%d").date()
    return ["월", "화", "수", "목", "금", "토", "일"][d.weekday()]


def build_week_timetable_table(grade, class_nm):
    school_info = get_school_info("광명고등학교", "경기도교육청")
    if not school_info:
        return None

    ay = str(datetime.date.today().year)
    sem = get_current_semester()

    week_days = get_week_dates(0)
    all_rows = []

    for day in week_days:
        ymd = day.strftime("%Y%m%d")
        rows = fetch_timetable_day(school_info, ay, sem, grade, class_nm, ymd)
        rows = normalize_timetable_rows(rows)

        for row in rows:
            all_rows.append({
                "요일": weekday_kor(row["date"]) if row["date"] else "",
                "교시": row["period"],
                "표시": f"{row['subject']} ({row['room']})" if row["room"] else row["subject"]
            })

    if not all_rows:
        return None

    weekdays = ["월", "화", "수", "목", "금"]

    periods = sorted(
        list({r["교시"] for r in all_rows}),
        key=lambda x: int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else 999
    )

    table = []
    for period in periods:
        row_data = {"교시": period}
        for wd in weekdays:
            row_data[wd] = ""

        for row in all_rows:
            if row["교시"] == period and row["요일"] in weekdays:
                row_data[row["요일"]] = row["표시"]

        table.append(row_data)

    return table


def get_current_user():
    if session.get("is_admin") is True:
        return {
            "id": 0,
            "username": ADMIN_USERNAME,
            "student_id": "ADMIN",
            "student_name": "광글 관리자",
            "role": "admin",
            "homeroom_teacher_id": None,
            "homeroom_teacher_name": None,
            "teacher_subject": None,
            "teacher_position": None,
        }

    user_id = session.get("user_id")
    if not user_id:
        return None

    conn = get_db()
    user = conn.execute("""
        SELECT
            users.id,
            users.username,
            users.student_id,
            users.student_name,
            users.homeroom_teacher_id,
            users.role,
            users.teacher_subject,
            users.teacher_position,
            teachers.title AS homeroom_teacher_name
        FROM users
        LEFT JOIN teachers
          ON users.homeroom_teacher_id = teachers.id
        WHERE users.id = ?
    """, (user_id,)).fetchone()
    conn.close()

    return user


def login_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            flash("로그인 후 이용할 수 있습니다.")
            return redirect(url_for("login_page"))
        return func(*args, **kwargs)
    return wrapper


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        current_user = get_current_user()
        if not current_user or current_user["role"] != "admin":
            flash("관리자만 접근할 수 있습니다.")
            return redirect(url_for("login_page"))
        return func(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_user():
    return {"current_user": get_current_user()}


GAME_CONFIGS = {
    "reaction": {
        "name": "반응속도 챌린지",
        "duration": 30,
        "min_ms": 20000,
        "max_ms": 75000,
        "max_score": 3500,
        "max_hits": 300,
        "max_misses": 400,
    },
    "dodge": {
        "name": "닷지 서바이벌",
        "duration": 45,
        "min_ms": 0,
        "max_ms": 90000,
        "max_score": 9000,
        "max_hits": 400,
        "max_misses": 300,
    },
    "typing": {
        "name": "스피드 단어 챌린지",
        "duration": 45,
        "min_ms": 30000,
        "max_ms": 90000,
        "max_score": 9000,
        "max_hits": 400,
        "max_misses": 400,
    }
}

_game_sessions = {}
_game_lock = threading.Lock()


def create_game_session(user_id, game_type):
    game_id = uuid.uuid4().hex

    with _game_lock:
        _game_sessions[game_id] = {
            "user_id": user_id,
            "game_type": game_type,
            "start_time": time.time(),
            "used": False
        }

    return game_id


def cleanup_game_sessions():
    now = time.time()

    with _game_lock:
        expired = []
        for game_id, info in _game_sessions.items():
            if now - info["start_time"] > 150:
                expired.append(game_id)

        for game_id in expired:
            _game_sessions.pop(game_id, None)


def validate_game_submit(game_id, user_id, game_type, score, hits, misses, combo_max, accuracy, duration_ms):
    cleanup_game_sessions()

    if game_type not in GAME_CONFIGS:
        return False, "올바르지 않은 게임 종류입니다."

    config = GAME_CONFIGS[game_type]

    with _game_lock:
        game_info = _game_sessions.get(game_id)

        if not game_info:
            return False, "게임 세션이 만료되었거나 올바르지 않습니다."

        if game_info["used"]:
            return False, "이미 제출된 게임 기록입니다."

        if game_info["user_id"] != user_id:
            return False, "본인의 게임 기록만 제출할 수 있습니다."

        if game_info["game_type"] != game_type:
            return False, "게임 종류가 일치하지 않습니다."

        elapsed = time.time() - game_info["start_time"]

        if elapsed < config["min_ms"] / 1000 - 5:
            return False, "게임 시간이 비정상적으로 짧습니다."

        if elapsed > config["max_ms"] / 1000 + 10:
            return False, "게임 제출 시간이 초과되었습니다."

        game_info["used"] = True

    if score < 0 or score > config["max_score"]:
        return False, "점수가 비정상적입니다."

    if hits < 0 or hits > config["max_hits"]:
        return False, "성공 기록이 비정상적입니다."

    if misses < 0 or misses > config["max_misses"]:
        return False, "실패 기록이 비정상적입니다."

    if combo_max < 0 or combo_max > config["max_hits"]:
        return False, "콤보 기록이 비정상적입니다."

    if accuracy < 0 or accuracy > 100:
        return False, "정확도 기록이 비정상적입니다."

    if duration_ms < config["min_ms"] or duration_ms > config["max_ms"]:
        return False, "게임 시간이 비정상적입니다."

    if game_type in ["reaction", "typing"] and hits == 0 and score > 0:
        return False, "점수 기록이 비정상적입니다."

    return True, "ok"


def get_game_leaderboard(game_type="reaction", limit=10):
    if game_type not in GAME_CONFIGS:
        game_type = "reaction"

    conn = get_db()
    rows = conn.execute("""
        SELECT
            gs.user_id,
            gs.student_name,
            gs.student_id,
            gs.game_type,
            gs.game_name,
            gs.score,
            gs.hits,
            gs.misses,
            gs.combo_max,
            gs.accuracy,
            gs.duration_ms,
            gs.created_at
        FROM game_scores gs
        JOIN (
            SELECT user_id, MAX(score) AS max_score
            FROM game_scores
            WHERE game_type = ?
            GROUP BY user_id
        ) best
        ON gs.user_id = best.user_id
        AND gs.score = best.max_score
        WHERE gs.game_type = ?
        AND gs.id = (
            SELECT id
            FROM game_scores
            WHERE user_id = gs.user_id
            AND game_type = gs.game_type
            AND score = gs.score
            ORDER BY combo_max DESC, accuracy DESC, created_at ASC
            LIMIT 1
        )
        ORDER BY gs.score DESC, gs.combo_max DESC, gs.accuracy DESC, gs.created_at ASC
        LIMIT ?
    """, (game_type, game_type, limit)).fetchall()
    conn.close()

    result = []
    rank = 1

    for row in rows:
        result.append({
            "rank": rank,
            "student_name": row["student_name"],
            "student_id": row["student_id"],
            "game_type": row["game_type"],
            "game_name": row["game_name"],
            "score": row["score"],
            "hits": row["hits"],
            "misses": row["misses"],
            "combo_max": row["combo_max"],
            "accuracy": round(row["accuracy"], 1),
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"]
        })
        rank += 1

    return result


def is_admission_query(query):
    keywords = [
        "대입", "입시", "진학", "대학", "학과", "전형",
        "수시", "정시", "학종", "교과", "논술",
        "내신 분석", "모고", "모의고사", "생기부", "학생부",
        "진로", "희망 학과", "희망학과", "입학", "상담",
        "컴공", "공대", "의대", "간호", "경영", "인문", "자연"
    ]
    return any(keyword in query for keyword in keywords)


def build_admission_context(user_id):
    user = get_current_user()
    grade, class_nm, number = parse_student_id(user["student_id"])

    grade_records = get_grade_records(user_id)
    mock_records = get_mock_exam_records(user_id)
    record_note = get_student_record_note(user_id)

    grade_lines = []
    if grade_records:
        for r in grade_records:
            grade_lines.append(
                f"- {r['semester']} / {r['subject']} / 점수: {r['score'] or '-'} / "
                f"등급: {r['grade_level'] or '-'} / 메모: {r['note'] or '-'}"
            )
    else:
        grade_lines.append("- 입력된 내신 기록 없음")

    mock_lines = []
    if mock_records:
        for r in mock_records:
            mock_lines.append(
                f"- {r['exam_name']} ({r['exam_date'] or '-'}) / "
                f"국어: {r['korean_grade'] or '-'} / 수학: {r['math_grade'] or '-'} / "
                f"영어: {r['english_grade'] or '-'} / 탐구: {r['inquiry_grade'] or '-'} / "
                f"메모: {r['note'] or '-'}"
            )
    else:
        mock_lines.append("- 입력된 모의고사 기록 없음")

    context = f"""
[학생 기본 정보]
학년/반/번호: {grade or '-'}학년 {class_nm or '-'}반 {number or '-'}번

[희망 진로]
진로 희망: {record_note['career_goal'] or '미입력'}
희망 학과: {record_note['target_major'] or '미입력'}
목표 대학/계열: {record_note['target_universities'] or '미입력'}

[내신 기록]
{chr(10).join(grade_lines)}

[모의고사 기록]
{chr(10).join(mock_lines)}

[생기부/학생부 요약]
{record_note['student_record_summary'] or '미입력'}

[활동 요약]
{record_note['activities_summary'] or '미입력'}

[강점]
{record_note['strengths'] or '미입력'}

[고민/보완점]
{record_note['concerns'] or '미입력'}
"""
    return context


def generate_admission_counseling(user_message):
    current_user = get_current_user()

    if not current_user:
        return "대입 상담은 로그인 후 이용할 수 있습니다. 먼저 로그인해주세요."

    if current_user["role"] != "student":
        return "대입 상담은 학생 계정에서 이용할 수 있습니다."

    if not OPENAI_API_KEY or not openai_client:
        return "OpenAI API 키가 설정되어 있지 않아 대입 상담을 생성할 수 없습니다."

    admission_context = build_admission_context(current_user["id"])

    prompt = f"""
아래 학생 데이터는 상담 참고용으로만 사용해라.
답변에서 이 데이터를 길게 다시 나열하지 마라.
이미 입력된 내신, 모의고사, 생기부 내용은 필요한 경우에만 짧게 언급해라.

{admission_context}

[학생 질문]
{user_message}
"""

    instructions = """
너는 고등학생을 위한 대입 상담 보조 챗봇이다.

답변 가능한 범위:
- 대입, 입시, 진학, 학과 선택
- 수시, 정시, 학생부종합, 교과, 논술
- 내신, 모의고사, 생기부, 세특, 비교과, 진로 설계

위 범위와 관련 없는 질문에는 답하지 말고 반드시 아래 문장만 출력해라:
"저는 대입 상담을 돕는 챗봇입니다. 입시, 진학, 내신, 모의고사, 생기부, 학과 선택과 관련된 질문만 답변할 수 있습니다."

합격 가능성, 등급컷, 특정 대학 합격 여부를 단정하지 마라.
학생이 입력한 정보를 그대로 길게 반복하지 마라.
핵심 판단, 보완할 점, 다음 행동 중심으로 500자 이내로 답해라.
"""

    try:
        response = openai_client.responses.create(
            model=OPENAI_MODEL,
            instructions=instructions,
            input=prompt
        )
        return response.output_text

    except Exception as e:
        return f"대입 상담 응답 중 오류가 발생했습니다: {str(e)}"


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/game")
def game_page():
    return render_template("game.html")


@app.route("/admin")
@admin_required
def admin_page():
    return render_template("admin.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("로그아웃되었습니다.")
    return redirect(url_for("home"))


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        login_role = request.form.get("loginRole", "student").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if login_role not in ["student", "teacher", "admin"]:
            flash("올바르지 않은 로그인 유형입니다.")
            return render_template("login.html")

        if not username or not password:
            flash("아이디와 비밀번호를 입력해주세요.")
            return render_template("login.html")

        if login_role == "admin":
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                session.clear()
                session["is_admin"] = True
                session["user_role"] = "admin"
                flash("관리자 계정으로 로그인되었습니다.")
                return redirect(url_for("admin_page"))

            flash("관리자 아이디 또는 비밀번호가 올바르지 않습니다.")
            return render_template("login.html")

        conn = get_db()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        conn.close()

        if not user:
            flash("존재하지 않는 아이디입니다.")
            return render_template("login.html")

        user_role = user["role"] if "role" in user.keys() else "student"

        if user_role != login_role:
            if login_role == "student":
                flash("학생 계정으로 가입된 아이디가 아닙니다.")
            elif login_role == "teacher":
                flash("선생님 계정으로 가입된 아이디가 아닙니다.")
            return render_template("login.html")

        if not check_password_hash(user["password_hash"], password):
            flash("비밀번호가 올바르지 않습니다.")
            return render_template("login.html")

        session.clear()
        session["user_id"] = user["id"]
        session["user_role"] = user_role

        if user_role == "teacher":
            flash(f"{user['student_name']} 선생님, 로그인되었습니다.")
        else:
            flash(f"{user['student_name']}님, 로그인되었습니다.")

        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup_page():
    if request.method == "POST":
        signup_role = request.form.get("signupRole", "student").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        password_confirm = request.form.get("passwordConfirm", "").strip()

        if signup_role not in ["student", "teacher"]:
            flash("회원가입 유형을 다시 선택해주세요.")
            return render_template("signup.html")

        if not username or not password or not password_confirm:
            flash("모든 항목을 입력해주세요.")
            return render_template("signup.html")

        if password != password_confirm:
            flash("비밀번호가 일치하지 않습니다.")
            return render_template("signup.html")

        if len(password) < 4:
            flash("비밀번호는 최소 4자 이상으로 설정해주세요.")
            return render_template("signup.html")

        conn = get_db()
        exists = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        conn.close()

        if exists:
            flash("이미 사용 중인 아이디입니다.")
            return render_template("signup.html")

        session["signup_temp"] = {
            "role": signup_role,
            "username": username,
            "password": password
        }
        return redirect(url_for("signup_step2_page"))

    return render_template("signup.html")


@app.route("/signup/step2", methods=["GET", "POST"])
def signup_step2_page():
    signup_temp = session.get("signup_temp")
    if not signup_temp:
        flash("회원가입 1단계부터 진행해주세요.")
        return redirect(url_for("signup_page"))

    signup_role = signup_temp.get("role", "student")
    teacher_list = get_all_teachers()

    if request.method == "POST":
        if signup_role == "student":
            student_id = request.form.get("studentId", "").strip()
            student_name = request.form.get("studentName", "").strip()
            homeroom_teacher_id = request.form.get("homeroomTeacherId", "").strip()

            if not student_id or not student_name or not homeroom_teacher_id:
                flash("학번, 이름, 담임선생님을 모두 입력해주세요.")
                return render_template("signup_step2.html", teachers=teacher_list, signup_role=signup_role)

            grade, class_nm, _ = parse_student_id(student_id)
            if not grade or not class_nm:
                flash("학번 형식을 확인해주세요. 예: 2101 또는 20101")
                return render_template("signup_step2.html", teachers=teacher_list, signup_role=signup_role)

            if not teacher_exists(homeroom_teacher_id):
                flash("담임선생님 정보를 확인해주세요.")
                return render_template("signup_step2.html", teachers=teacher_list, signup_role=signup_role)

            conn = get_db()

            student_exists = conn.execute(
                "SELECT id FROM users WHERE student_id = ?",
                (student_id,)
            ).fetchone()

            if student_exists:
                conn.close()
                flash("이미 가입된 학번입니다.")
                return render_template("signup_step2.html", teachers=teacher_list, signup_role=signup_role)

            password_hash = generate_password_hash(signup_temp["password"])

            conn.execute("""
                INSERT INTO users (
                    username, password_hash, student_id, student_name,
                    homeroom_teacher_id, role, teacher_subject, teacher_position, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signup_temp["username"],
                password_hash,
                student_id,
                student_name,
                int(homeroom_teacher_id),
                "student",
                None,
                None,
                datetime.datetime.now().isoformat()
            ))
            conn.commit()

            user = conn.execute(
                "SELECT id FROM users WHERE username = ?",
                (signup_temp["username"],)
            ).fetchone()
            conn.close()

            session.pop("signup_temp", None)
            session["user_id"] = user["id"]
            session["user_role"] = "student"

            flash(f"{student_name}님, 학생 회원가입이 완료되었습니다.")
            return redirect(url_for("home"))

        if signup_role == "teacher":
            teacher_name = request.form.get("teacherName", "").strip()
            teacher_subject = request.form.get("teacherSubject", "").strip()
            teacher_position = request.form.get("teacherPosition", "").strip()

            if not teacher_name:
                flash("선생님 이름을 입력해주세요.")
                return render_template("signup_step2.html", teachers=teacher_list, signup_role=signup_role)

            generated_teacher_id = f"TEACHER-{signup_temp['username']}"

            conn = get_db()

            teacher_id_exists = conn.execute(
                "SELECT id FROM users WHERE student_id = ?",
                (generated_teacher_id,)
            ).fetchone()

            if teacher_id_exists:
                conn.close()
                flash("이미 가입된 선생님 계정입니다.")
                return render_template("signup_step2.html", teachers=teacher_list, signup_role=signup_role)

            password_hash = generate_password_hash(signup_temp["password"])

            conn.execute("""
                INSERT INTO users (
                    username, password_hash, student_id, student_name,
                    homeroom_teacher_id, role, teacher_subject, teacher_position, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signup_temp["username"],
                password_hash,
                generated_teacher_id,
                teacher_name,
                None,
                "teacher",
                teacher_subject,
                teacher_position,
                datetime.datetime.now().isoformat()
            ))
            conn.commit()

            user = conn.execute(
                "SELECT id FROM users WHERE username = ?",
                (signup_temp["username"],)
            ).fetchone()
            conn.close()

            session.pop("signup_temp", None)
            session["user_id"] = user["id"]
            session["user_role"] = "teacher"

            flash(f"{teacher_name} 선생님, 회원가입이 완료되었습니다.")
            return redirect(url_for("home"))

    return render_template("signup_step2.html", teachers=teacher_list, signup_role=signup_role)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile_page():
    current_user = get_current_user()

    if current_user["role"] == "admin":
        return redirect(url_for("admin_page"))

    teacher_list = get_all_teachers()

    if request.method == "POST":
        action = request.form.get("action", "").strip()

        if current_user["role"] == "teacher":
            if action == "update_profile":
                teacher_name = request.form.get("studentName", "").strip()
                teacher_subject = request.form.get("teacherSubject", "").strip()
                teacher_position = request.form.get("teacherPosition", "").strip()

                if not teacher_name:
                    flash("이름을 입력해주세요.")
                    return redirect(url_for("profile_page"))

                conn = get_db()
                conn.execute("""
                    UPDATE users
                    SET student_name = ?, teacher_subject = ?, teacher_position = ?
                    WHERE id = ?
                """, (
                    teacher_name,
                    teacher_subject,
                    teacher_position,
                    current_user["id"]
                ))
                conn.commit()
                conn.close()

                flash("선생님 프로필 정보가 수정되었습니다.")
                return redirect(url_for("profile_page"))

        if action == "update_profile":
            student_name = request.form.get("studentName", "").strip()
            student_id = request.form.get("studentId", "").strip()
            homeroom_teacher_id = request.form.get("homeroomTeacherId", "").strip()

            if not student_name or not student_id or not homeroom_teacher_id:
                flash("이름, 학번, 담임선생님을 모두 입력해주세요.")
                return redirect(url_for("profile_page"))

            grade, class_nm, _ = parse_student_id(student_id)
            if not grade or not class_nm:
                flash("학번 형식을 확인해주세요. 예: 2101 또는 20101")
                return redirect(url_for("profile_page"))

            if not teacher_exists(homeroom_teacher_id):
                flash("담임선생님 정보를 확인해주세요.")
                return redirect(url_for("profile_page"))

            conn = get_db()
            duplicate = conn.execute(
                "SELECT id FROM users WHERE student_id = ? AND id != ?",
                (student_id, current_user["id"])
            ).fetchone()

            if duplicate:
                conn.close()
                flash("이미 다른 계정에서 사용 중인 학번입니다.")
                return redirect(url_for("profile_page"))

            conn.execute("""
                UPDATE users
                SET student_name = ?, student_id = ?, homeroom_teacher_id = ?
                WHERE id = ?
            """, (
                student_name,
                student_id,
                int(homeroom_teacher_id),
                current_user["id"]
            ))

            conn.commit()
            conn.close()

            flash("프로필 정보가 수정되었습니다.")
            return redirect(url_for("profile_page"))

        if action == "change_password":
            current_password = request.form.get("currentPassword", "").strip()
            new_password = request.form.get("newPassword", "").strip()
            new_password_confirm = request.form.get("newPasswordConfirm", "").strip()

            if not current_password or not new_password or not new_password_confirm:
                flash("비밀번호 항목을 모두 입력해주세요.")
                return redirect(url_for("profile_page"))

            if new_password != new_password_confirm:
                flash("새 비밀번호가 일치하지 않습니다.")
                return redirect(url_for("profile_page"))

            if len(new_password) < 4:
                flash("새 비밀번호는 최소 4자 이상으로 설정해주세요.")
                return redirect(url_for("profile_page"))

            conn = get_db()
            user = conn.execute(
                "SELECT * FROM users WHERE id = ?",
                (current_user["id"],)
            ).fetchone()

            if not check_password_hash(user["password_hash"], current_password):
                conn.close()
                flash("현재 비밀번호가 올바르지 않습니다.")
                return redirect(url_for("profile_page"))

            new_hash = generate_password_hash(new_password)

            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (new_hash, current_user["id"])
            )
            conn.commit()
            conn.close()

            flash("비밀번호가 변경되었습니다.")
            return redirect(url_for("profile_page"))

        if current_user["role"] != "student":
            flash("해당 기능은 학생 계정에서 이용할 수 있습니다.")
            return redirect(url_for("profile_page"))

        if action == "add_grade":
            semester = request.form.get("semester", "").strip()
            subject = request.form.get("subject", "").strip()
            score = request.form.get("score", "").strip()
            grade_level = request.form.get("gradeLevel", "").strip()
            note = request.form.get("note", "").strip()

            if not semester or not subject:
                flash("학기와 과목명은 필수입니다.")
                return redirect(url_for("profile_page"))

            conn = get_db()
            conn.execute("""
                INSERT INTO grade_records (
                    user_id, semester, subject, score, grade_level, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                current_user["id"],
                semester,
                subject,
                score,
                grade_level,
                note,
                datetime.datetime.now().isoformat()
            ))

            conn.commit()
            conn.close()

            flash("내신 기록이 추가되었습니다.")
            return redirect(url_for("profile_page"))

        if action == "delete_grade":
            grade_record_id = request.form.get("gradeRecordId", "").strip()

            conn = get_db()
            conn.execute(
                "DELETE FROM grade_records WHERE id = ? AND user_id = ?",
                (grade_record_id, current_user["id"])
            )
            conn.commit()
            conn.close()

            flash("내신 기록이 삭제되었습니다.")
            return redirect(url_for("profile_page"))

        if action == "add_mock_exam":
            exam_name = request.form.get("examName", "").strip()
            exam_date = request.form.get("examDate", "").strip()
            korean_grade = request.form.get("koreanGrade", "").strip()
            math_grade = request.form.get("mathGrade", "").strip()
            english_grade = request.form.get("englishGrade", "").strip()
            inquiry_grade = request.form.get("inquiryGrade", "").strip()
            note = request.form.get("mockNote", "").strip()

            if not exam_name:
                flash("모의고사 이름은 필수입니다.")
                return redirect(url_for("profile_page"))

            conn = get_db()
            conn.execute("""
                INSERT INTO mock_exam_records (
                    user_id, exam_name, exam_date, korean_grade, math_grade,
                    english_grade, inquiry_grade, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                current_user["id"],
                exam_name,
                exam_date,
                korean_grade,
                math_grade,
                english_grade,
                inquiry_grade,
                note,
                datetime.datetime.now().isoformat()
            ))
            conn.commit()
            conn.close()

            flash("모의고사 기록이 추가되었습니다.")
            return redirect(url_for("profile_page"))

        if action == "delete_mock_exam":
            mock_exam_id = request.form.get("mockExamId", "").strip()

            conn = get_db()
            conn.execute(
                "DELETE FROM mock_exam_records WHERE id = ? AND user_id = ?",
                (mock_exam_id, current_user["id"])
            )
            conn.commit()
            conn.close()

            flash("모의고사 기록이 삭제되었습니다.")
            return redirect(url_for("profile_page"))

        if action == "update_student_record":
            career_goal = request.form.get("careerGoal", "").strip()
            target_major = request.form.get("targetMajor", "").strip()
            target_universities = request.form.get("targetUniversities", "").strip()
            student_record_summary = request.form.get("studentRecordSummary", "").strip()
            activities_summary = request.form.get("activitiesSummary", "").strip()
            strengths = request.form.get("strengths", "").strip()
            concerns = request.form.get("concerns", "").strip()

            conn = get_db()
            conn.execute("""
                INSERT INTO student_record_notes (
                    user_id, career_goal, target_major, target_universities,
                    student_record_summary, activities_summary, strengths, concerns, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    career_goal = excluded.career_goal,
                    target_major = excluded.target_major,
                    target_universities = excluded.target_universities,
                    student_record_summary = excluded.student_record_summary,
                    activities_summary = excluded.activities_summary,
                    strengths = excluded.strengths,
                    concerns = excluded.concerns,
                    updated_at = excluded.updated_at
            """, (
                current_user["id"],
                career_goal,
                target_major,
                target_universities,
                student_record_summary,
                activities_summary,
                strengths,
                concerns,
                datetime.datetime.now().isoformat()
            ))
            conn.commit()
            conn.close()

            flash("대입 상담용 기록이 저장되었습니다.")
            return redirect(url_for("profile_page"))

        flash("올바르지 않은 요청입니다.")
        return redirect(url_for("profile_page"))

    refreshed_user = get_current_user()
    grade, class_nm, number = parse_student_id(refreshed_user["student_id"])
    grade_records = get_grade_records(refreshed_user["id"]) if refreshed_user["role"] == "student" else []
    mock_exam_records = get_mock_exam_records(refreshed_user["id"]) if refreshed_user["role"] == "student" else []
    student_record_note = get_student_record_note(refreshed_user["id"]) if refreshed_user["role"] == "student" else get_student_record_note(-1)

    return render_template(
        "profile.html",
        profile_user=refreshed_user,
        teachers=teacher_list,
        grade=grade,
        class_nm=class_nm,
        number=number,
        grade_records=grade_records,
        mock_exam_records=mock_exam_records,
        student_record_note=student_record_note
    )


@app.route("/timetable")
def timetable_page():
    grade = request.args.get("grade", "").strip()
    class_nm = request.args.get("class", "").strip()

    if not grade or not class_nm:
        current_user = get_current_user()
        if current_user and current_user["role"] == "student" and current_user["student_id"]:
            parsed_grade, parsed_class, _ = parse_student_id(current_user["student_id"])
            if parsed_grade and parsed_class:
                grade = parsed_grade
                class_nm = parsed_class

    return render_template("timetable.html", grade=grade, class_nm=class_nm)


@app.route("/meal")
def meal_page():
    mode = request.args.get("mode", "today")
    return render_template("meal.html", mode=mode)


@app.route("/teachers")
def teachers_page():
    teacher_list = get_all_teachers()
    return render_template("teachers.html", teachers=teacher_list)


@app.route("/chatbot")
def chatbot_page():
    return render_template("chatbot.html")


@app.route("/api/game/start", methods=["POST"])
def game_start_api():
    current_user = get_current_user()

    if not current_user:
        return jsonify({
            "success": False,
            "message": "로그인 후 랭킹 게임을 시작할 수 있습니다."
        }), 401

    if current_user["role"] == "admin":
        return jsonify({
            "success": False,
            "message": "관리자 계정으로는 게임을 시작할 수 없습니다."
        }), 403

    data = request.get_json(silent=True) or {}
    game_type = data.get("gameType", "reaction")

    if game_type not in GAME_CONFIGS:
        return jsonify({
            "success": False,
            "message": "올바르지 않은 게임 종류입니다."
        }), 400

    game_id = create_game_session(current_user["id"], game_type)

    return jsonify({
        "success": True,
        "game_id": game_id,
        "game_type": game_type,
        "game_name": GAME_CONFIGS[game_type]["name"],
        "duration_seconds": GAME_CONFIGS[game_type]["duration"],
        "player": {
            "student_name": current_user["student_name"],
            "student_id": current_user["student_id"]
        }
    })


@app.route("/api/game/submit", methods=["POST"])
def game_submit_api():
    current_user = get_current_user()

    if not current_user:
        return jsonify({
            "success": False,
            "message": "로그인 후 점수를 제출할 수 있습니다."
        }), 401

    if current_user["role"] == "admin":
        return jsonify({
            "success": False,
            "message": "관리자 계정으로는 점수를 제출할 수 없습니다."
        }), 403

    data = request.get_json(silent=True) or {}

    try:
        game_id = data.get("gameId", "")
        game_type = data.get("gameType", "reaction")
        score = int(data.get("score", 0))
        hits = int(data.get("hits", 0))
        misses = int(data.get("misses", 0))
        combo_max = int(data.get("comboMax", 0))
        accuracy = float(data.get("accuracy", 0))
        duration_ms = int(data.get("durationMs", 0))
    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "게임 기록 형식이 올바르지 않습니다."
        }), 400

    valid, message = validate_game_submit(
        game_id,
        current_user["id"],
        game_type,
        score,
        hits,
        misses,
        combo_max,
        accuracy,
        duration_ms
    )

    if not valid:
        return jsonify({
            "success": False,
            "message": message
        }), 400

    game_name = GAME_CONFIGS[game_type]["name"]

    conn = get_db()
    conn.execute("""
        INSERT INTO game_scores (
            user_id, student_name, student_id, game_type, game_name,
            score, hits, misses, combo_max, accuracy, duration_ms, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        current_user["id"],
        current_user["student_name"],
        current_user["student_id"],
        game_type,
        game_name,
        score,
        hits,
        misses,
        combo_max,
        accuracy,
        duration_ms,
        datetime.datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()

    leaderboard = get_game_leaderboard(game_type, 10)

    return jsonify({
        "success": True,
        "message": "점수가 저장되었습니다.",
        "leaderboard": leaderboard
    })


@app.route("/api/game/leaderboard")
def game_leaderboard_api():
    game_type = request.args.get("gameType", "reaction")
    limit = request.args.get("limit", "10")

    if game_type not in GAME_CONFIGS:
        game_type = "reaction"

    try:
        limit = int(limit)
    except ValueError:
        limit = 10

    limit = max(1, min(limit, 20))

    return jsonify({
        "success": True,
        "game_type": game_type,
        "game_name": GAME_CONFIGS[game_type]["name"],
        "leaderboard": get_game_leaderboard(game_type, limit)
    })


@app.route("/api/login-student", methods=["POST"])
def login_student():
    data = request.get_json(silent=True) or {}
    student_id = data.get("student_id", "")

    grade, class_nm, number = parse_student_id(student_id)

    if not grade or not class_nm:
        return jsonify({
            "success": False,
            "message": "학번 형식을 확인해주세요."
        }), 400

    return jsonify({
        "success": True,
        "grade": grade,
        "class_nm": class_nm,
        "number": number,
        "redirect_url": f"/timetable?grade={grade}&class={class_nm}&student={student_id}"
    })


@app.route("/api/timetable")
def timetable_api():
    grade = request.args.get("grade", "").strip()
    class_nm = request.args.get("class", "").strip()

    if not grade or not class_nm:
        return jsonify({
            "success": False,
            "message": "학년과 반 정보가 필요합니다."
        }), 400

    table = build_week_timetable_table(grade, class_nm)

    if not table:
        return jsonify({
            "success": False,
            "message": "시간표 정보를 불러오지 못했습니다."
        }), 404

    return jsonify({
        "success": True,
        "title": f"{grade}학년 {class_nm}반 이번 주 시간표",
        "table": table
    })


@app.route("/api/meal")
def meal_api():
    mode = request.args.get("mode", "today")

    if mode == "today":
        if is_weekend():
            week_meals = get_week_meals("광명고등학교", "경기도교육청", offset_weeks=1)
            return jsonify({
                "success": True,
                "title": "다음 주 급식",
                "desc": "주말이어서 다음 주 전체 급식을 보여줍니다.",
                "items": week_meals
            })

        today = datetime.date.today()
        menus = get_menus_by_day(
            "광명고등학교",
            "경기도교육청",
            today.strftime("%Y%m%d")
        )

        return jsonify({
            "success": True,
            "title": "오늘의 급식",
            "desc": "광명고등학교 오늘 급식 메뉴입니다.",
            "items": [{
                "date": today.strftime("%Y-%m-%d"),
                "weekday": ["월", "화", "수", "목", "금", "토", "일"][today.weekday()],
                "menus": menus
            }]
        })

    if mode == "week":
        week_meals = get_week_meals("광명고등학교", "경기도교육청", offset_weeks=0)
        return jsonify({
            "success": True,
            "title": "이번 주 급식",
            "desc": "광명고등학교 이번 주 급식입니다.",
            "items": week_meals
        })

    if mode == "next":
        week_meals = get_week_meals("광명고등학교", "경기도교육청", offset_weeks=1)
        return jsonify({
            "success": True,
            "title": "다음 주 급식",
            "desc": "광명고등학교 다음 주 급식입니다.",
            "items": week_meals
        })

    return jsonify({
        "success": False,
        "message": "올바르지 않은 요청입니다."
    }), 400


@app.route("/api/search")
def search():
    query = request.args.get("query", "").strip().lower()
    search_data = get_search_data()

    calendar_result = build_calendar_search_result(query)
    if calendar_result:
        return jsonify([calendar_result])

    if query in ["게임", "미니게임", "광글 게임", "반응속도 게임", "닷지 게임", "타자 게임", "랭킹 게임"]:
        return jsonify([{
            "type": "미니게임",
            "title": "광글 미니게임",
            "desc": "반응속도, 닷지, 스피드 단어 게임으로 친구들과 랭킹을 겨룹니다.",
            "link": "/game"
        }])

    if query in ["오늘 급식", "급식", "오늘급식"]:
        return jsonify([{
            "type": "급식 정보",
            "title": "이번 주 급식 페이지로 이동",
            "desc": "광명고등학교 이번 주 급식을 확인합니다.",
            "link": "/meal?mode=week"
        }])

    if query in ["이번주 급식", "이번 주 급식"]:
        return jsonify([{
            "type": "급식 정보",
            "title": "이번 주 급식 페이지로 이동",
            "desc": "광명고등학교 이번 주 급식을 확인합니다.",
            "link": "/meal?mode=week"
        }])

    if query in ["다음주 급식", "다음 주 급식"]:
        return jsonify([{
            "type": "급식 정보",
            "title": "다음 주 급식 페이지로 이동",
            "desc": "광명고등학교 다음 주 급식을 확인합니다.",
            "link": "/meal?mode=next"
        }])

    if query in ["선생님 목록", "선생님", "교사 목록", "선생님들"]:
        return jsonify([{
            "type": "선생님 정보",
            "title": "선생님 목록 페이지로 이동",
            "desc": "광명고 선생님 목록을 확인합니다.",
            "link": "/teachers"
        }])

    if query in ["챗봇", "학교 챗봇", "광글 챗봇", "대입 상담", "입시 상담"]:
        return jsonify([{
            "type": "챗봇",
            "title": "학교 정보 챗봇 페이지로 이동",
            "desc": "학교 정보와 대입 상담을 함께 이용할 수 있습니다.",
            "link": "/chatbot"
        }])

    if query in ["프로필", "내 정보", "마이페이지", "개인정보", "생기부 입력", "모의고사 입력"]:
        return jsonify([{
            "type": "프로필",
            "title": "프로필 페이지로 이동",
            "desc": "내신, 모의고사, 생기부 요약, 희망 진로를 입력하고 관리합니다.",
            "link": "/profile"
        }])

    if query in ["관리자", "관리자 페이지", "광글 관리자"]:
        return jsonify([{
            "type": "관리자",
            "title": "관리자 페이지",
            "desc": "관리자 계정으로 로그인한 경우 관리자 페이지로 이동합니다.",
            "link": "/admin"
        }])

    grade, class_nm = extract_grade_class(query)
    if grade and class_nm and "시간표" in query:
        return jsonify([{
            "type": "시간표 정보",
            "title": f"{grade}학년 {class_nm}반 시간표",
            "desc": "시간표 페이지로 이동합니다.",
            "link": f"/timetable?grade={grade}&class={class_nm}"
        }])

    results = []
    for item in search_data:
        if is_match(query, item):
            results.append(item)

    return jsonify(results)


@app.route("/api/suggest")
def suggest():
    query = request.args.get("query", "").strip().lower()
    search_data = get_search_data()

    keywords = set()
    for item in search_data:
        title = item.get("title", "")
        if title:
            keywords.add(title)

        for keyword in item.get("keywords", []):
            keywords.add(keyword)

    fixed_keywords = [
        "오늘 급식", "이번 주 급식", "다음 주 급식",
        "선생님 목록", "챗봇", "학교 챗봇",
        "게임", "미니게임", "광글 게임", "반응속도 게임", "닷지 게임", "타자 게임",
        "프로필", "내 정보", "마이페이지",
        "관리자", "관리자 페이지",
        "대입 상담", "입시 상담", "내신 분석", "모의고사 분석",
        "생기부 분석", "학종 상담", "수시 상담", "정시 상담",
        "학사일정", "전체 학사일정", "연간 학사일정",
        "5월 일정", "6월 일정", "7월 일정", "8월 일정",
        "9월 일정", "10월 일정", "11월 일정", "12월 일정",
        "시험 일정", "방학 일정", "개학 일정",
        "1학년 1반 시간표", "2학년 1반 시간표", "3학년 1반 시간표"
    ]

    for keyword in fixed_keywords:
        keywords.add(keyword)

    query_tokens = query.split()

    suggestions = []
    for k in keywords:
        lower_k = k.lower()
        if query in lower_k or all(token in lower_k for token in query_tokens):
            suggestions.append(k)

    suggestions = sorted(set(suggestions), key=lambda x: len(x))[:8]

    return jsonify(suggestions)


def format_week_meals_text(title, week_meals):
    lines = [title]
    for item in week_meals:
        menu_text = ", ".join(item["menus"]) if item["menus"] else "급식 정보 없음"
        lines.append(f"{item['date']} ({item['weekday']}): {menu_text}")
    return "\n".join(lines)


def format_timetable_text(title, table):
    if not table:
        return "시간표 정보를 불러오지 못했습니다."

    headers = ["교시", "월", "화", "수", "목", "금"]
    lines = [title]

    for row in table:
        line_parts = []
        for h in headers:
            value = row.get(h, "")
            line_parts.append(f"{h}: {value if value else '-'}")
        lines.append(" | ".join(line_parts))

    return "\n".join(lines)


def search_local_info(query):
    search_data = get_search_data()
    matched = []

    for item in search_data:
        if is_match(query, item):
            matched.append(item)

    return matched[:5]


def build_local_search_answer(query):
    matched = search_local_info(query)
    if not matched:
        return None

    lines = []
    for item in matched:
        line = f"{item.get('title', '')}: {item.get('desc', '')}"
        tags = item.get("tags", [])
        if tags:
            line += f" / 태그: {', '.join(tags)}"
        lines.append(line)

    return "\n".join(lines)


def handle_chat_locally(user_message):
    query = user_message.strip().lower()

    if any(word in query for word in ["게임", "미니게임", "랭킹 게임", "반응속도 게임", "닷지 게임", "타자 게임"]):
        return "광글 미니게임은 /game 페이지에서 플레이할 수 있습니다. 로그인하면 점수가 학번과 이름으로 랭킹에 저장됩니다."

    if any(word in query for word in ["프로필", "내 정보", "마이페이지", "개인정보"]):
        current_user = get_current_user()
        if not current_user:
            return "프로필은 로그인 후 확인할 수 있습니다. 먼저 로그인해주세요."

        if current_user["role"] == "teacher":
            return "선생님 계정은 프로필에서 이름, 담당 과목, 직책, 비밀번호를 관리할 수 있습니다."

        return "프로필 페이지에서 이름, 학번, 담임선생님, 비밀번호, 내신, 모의고사, 생기부 요약을 관리할 수 있습니다."

    if is_calendar_query(query):
        if is_all_calendar_query(query):
            return format_all_calendar_events_text()

        month = extract_calendar_month(query)
        if month:
            return format_calendar_events_text(month)

        matched_events = search_calendar_events_by_keyword(query)
        if matched_events:
            return format_calendar_keyword_result(matched_events)

        return format_all_calendar_events_text()

    if query in ["오늘 급식", "급식", "오늘급식", "이번 주 급식", "이번주 급식"]:
        week_meals = get_week_meals("광명고등학교", "경기도교육청", offset_weeks=0)
        return format_week_meals_text("이번 주 급식", week_meals)

    if query in ["다음 주 급식", "다음주 급식"]:
        week_meals = get_week_meals("광명고등학교", "경기도교육청", offset_weeks=1)
        return format_week_meals_text("다음 주 급식", week_meals)

    if "내 시간표" in query or "제 시간표" in query:
        current_user = get_current_user()
        if not current_user:
            return "로그인 후 내 시간표를 확인할 수 있습니다."

        if current_user["role"] != "student":
            return "내 시간표 기능은 학생 계정에서 학번을 기준으로 확인할 수 있습니다."

        grade, class_nm, _ = parse_student_id(current_user["student_id"])
        if not grade or not class_nm:
            return "학번에서 학년/반 정보를 해석하지 못했습니다."

        table = build_week_timetable_table(grade, class_nm)
        return format_timetable_text(f"{grade}학년 {class_nm}반 이번 주 시간표", table)

    grade, class_nm = extract_grade_class(query)
    if grade and class_nm and "시간표" in query:
        table = build_week_timetable_table(grade, class_nm)
        return format_timetable_text(f"{grade}학년 {class_nm}반 이번 주 시간표", table)

    location_keywords = [
        "선생님", "교사", "학생부", "보건실", "행정실", "교무실",
        "방송실", "도서관", "시설", "위치", "목록", "어디"
    ]
    if any(keyword in query for keyword in location_keywords):
        local_answer = build_local_search_answer(query)
        if local_answer:
            return local_answer

    return None


@app.route("/api/chat", methods=["POST"])
def chat_api():
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({
            "success": False,
            "reply": "질문을 입력해주세요."
        }), 400

    query = user_message.lower()

    local_reply = handle_chat_locally(user_message)
    if local_reply:
        return jsonify({
            "success": True,
            "reply": local_reply
        })

    if is_admission_query(query):
        reply = generate_admission_counseling(user_message)
        return jsonify({
            "success": True,
            "reply": reply
        })

    if not OPENAI_API_KEY or not openai_client:
        return jsonify({
            "success": False,
            "reply": "현재 챗봇 API가 설정되지 않았고, 로컬에서도 답을 찾지 못했습니다."
        }), 500

    teacher_list = get_all_teachers()

    teacher_summary = []
    for teacher in teacher_list[:60]:
        teacher_summary.append(f"- {teacher['title']}: {teacher['desc']}")

    facility_summary = []
    for facility in facilities[:40]:
        facility_summary.append(f"- {facility.get('title', '')}: {facility.get('desc', '')}")

    calendar_summary = []
    for event in get_all_calendar_events():
        calendar_summary.append(f"- {event['date']}: {event['title']}")

    current_user = get_current_user()
    user_context = ""
    if current_user:
        user_context = (
            f"현재 로그인 사용자 정보: "
            f"이름={current_user['student_name']}, "
            f"아이디/학번={current_user['student_id']}, "
            f"역할={current_user['role']}, "
            f"담임={current_user['homeroom_teacher_name'] or '없음'}"
        )

    context_text = f"""
광명고 학교 정보:
[선생님 정보]
{chr(10).join(teacher_summary)}

[시설 정보]
{chr(10).join(facility_summary)}

[학사일정]
{chr(10).join(calendar_summary)}

[로그인 사용자]
{user_context if user_context else '로그인 사용자 없음'}
"""

    try:
        response = openai_client.responses.create(
            model=OPENAI_MODEL,
            instructions=(
                "너는 광명고 학교 정보 도우미다. "
                "학교 관련 질문에만 답해라. "
                "주어진 학교 정보 문맥을 우선 사용해라. "
                "모르는 내용은 추측하지 말고 모른다고 말해라. "
                "답변은 짧고 친절하게 해라."
            ),
            input=f"{context_text}\n\n[사용자 질문]\n{user_message}"
        )

        return jsonify({
            "success": True,
            "reply": response.output_text
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "reply": f"챗봇 응답 중 오류가 발생했습니다: {str(e)}"
        }), 500

# ==============================
# Admin Management + Promotion Extension
# 홍보 요청 / 홍보 이미지 / 관리자 승인 / 메인 팝업 / 회원 제재 / 실시간 통계
# ==============================

PROMO_UPLOAD_DIR = os.path.join("static", "uploads", "promos")
PROMO_MAX_IMAGE_BYTES = 1 * 1024 * 1024
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

    if not column_exists(conn, "promo_requests", "promo_what"):
        cur.execute("ALTER TABLE promo_requests ADD COLUMN promo_what TEXT")

    if not column_exists(conn, "promo_requests", "promo_reason"):
        cur.execute("ALTER TABLE promo_requests ADD COLUMN promo_reason TEXT")

    if not column_exists(conn, "promo_requests", "image_path"):
        cur.execute("ALTER TABLE promo_requests ADD COLUMN image_path TEXT")

    if not column_exists(conn, "promo_requests", "image_original_name"):
        cur.execute("ALTER TABLE promo_requests ADD COLUMN image_original_name TEXT")

    if not column_exists(conn, "promo_requests", "image_size_bytes"):
        cur.execute("ALTER TABLE promo_requests ADD COLUMN image_size_bytes INTEGER")

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
        return None, None, None, "홍보물 이미지는 1MB 이하로 업로드해주세요."

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
                log_usage_event("search", "검색", {
                    "query": query
                })

        elif request.path == "/api/chat" and request.method == "POST":
            data = request.get_json(silent=True) or {}
            message = data.get("message", "")
            log_usage_event("chat", "챗봇", {
                "message_length": len(message)
            })

        elif request.path == "/api/meal":
            log_usage_event("tool", "급식", {
                "mode": request.args.get("mode", "today")
            })

        elif request.path == "/api/timetable":
            log_usage_event("tool", "시간표", {
                "grade": request.args.get("grade", ""),
                "class": request.args.get("class", "")
            })

        elif request.path == "/api/game/start":
            data = request.get_json(silent=True) or {}
            log_usage_event("game", "미니게임 시작", {
                "game_type": data.get("gameType", "")
            })

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
            log_usage_event("promo", "홍보 요청", {})

        elif request.path.startswith("/promotion/"):
            log_usage_event("promo", "홍보 상세", {
                "path": request.path
            })

    except Exception:
        pass

    return response


@app.route("/promo/request", methods=["GET", "POST"])
@login_required
def promo_request_page():
    current_user = get_current_user()

    if current_user["role"] == "admin":
        flash("관리자는 홍보 요청을 제출할 수 없습니다.")
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

        flash("홍보 요청이 관리자에게 제출되었습니다.")
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
            "title": "승인 대기 홍보 요청",
            "message": f"{pending_promos}개의 홍보 요청이 승인 대기 중입니다."
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
            "message": "홍보 요청 정보가 올바르지 않습니다."
        }), 400

    status = "approved" if action == "approve" else "rejected"

    conn = get_db()
    target = conn.execute("""
        SELECT id, COALESCE(promo_what, title) AS promo_what, status
        FROM promo_requests
        WHERE id = ?
    """, (promo_request_id,)).fetchone()

    if not target:
        conn.close()
        return jsonify({
            "success": False,
            "message": "홍보 요청을 찾을 수 없습니다."
        }), 404

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
        message = "홍보 요청을 승인했습니다. 이제 메인 팝업에 랜덤으로 표시될 수 있습니다."
    else:
        message = "홍보 요청을 거절했습니다."

    return jsonify({
        "success": True,
        "message": message,
        "data": fetch_admin_dashboard_data()
    })
if __name__ == "__main__":
    app.run(debug=True)
