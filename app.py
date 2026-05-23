from flask import Flask, render_template, request, jsonify, redirect, url_for, session, flash
import json
import os
import re
import sqlite3
import requests
import datetime
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
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1").strip()
BASE_URL = "https://open.neis.go.kr/hub/"
DATABASE = "gwangle.db"

openai_client = OpenAI(api_key=OPENAI_API_KEY) if OpenAI and OPENAI_API_KEY else None


def load_json_file(path, default):
    if not os.path.exists(path):
        return default

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


facilities = load_json_file("data/facilities.json", [])
academic_calendar = load_json_file("data/academic_calendar.json", [])


# -------------------------
# DB
# -------------------------

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


# -------------------------
# DB 데이터 변환
# -------------------------

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
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM teachers WHERE id = ?",
        (teacher_id,)
    ).fetchone()
    conn.close()
    return row is not None


def get_grade_records(user_id):
    conn = get_db()
    rows = conn.execute("""
        SELECT id, semester, subject, score, grade_level, note, created_at
        FROM grade_records
        WHERE user_id = ?
        ORDER BY created_at DESC, id DESC
    """, (user_id,)).fetchall()
    conn.close()
    return rows


# -------------------------
# 검색 유틸
# -------------------------

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


# -------------------------
# 학사일정
# -------------------------

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


def format_calendar_events_text(month):
    calendar_item = get_calendar_events_by_month(month)

    if not calendar_item:
        return f"{month}월 학사일정 정보가 없습니다."

    events = calendar_item.get("events", [])
    if not events:
        return f"{month}월 학사일정이 없습니다."

    lines = [f"2026년 {month}월 학사일정"]
    for event in events:
        lines.append(f"- {event['date']}: {event['title']}")

    return "\n".join(lines)


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

    month = extract_calendar_month(query_lower)
    if month:
        calendar_item = get_calendar_events_by_month(month)
        if not calendar_item:
            return None

        events = calendar_item.get("events", [])
        desc = "\n".join([f"{event['date']}: {event['title']}" for event in events])

        return {
            "type": "학사일정",
            "title": f"2026년 {month}월 학사일정",
            "desc": desc,
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

    return None


# -------------------------
# 급식
# -------------------------

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


# -------------------------
# 시간표
# -------------------------

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


# -------------------------
# 로그인 관련
# -------------------------

def get_current_user():
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


@app.context_processor
def inject_user():
    return {"current_user": get_current_user()}


@app.route("/logout")
def logout():
    session.clear()
    flash("로그아웃되었습니다.")
    return redirect(url_for("home"))


# -------------------------
# 페이지 라우트
# -------------------------

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("아이디와 비밀번호를 입력해주세요.")
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

        if not check_password_hash(user["password_hash"], password):
            flash("비밀번호가 올바르지 않습니다.")
            return render_template("login.html")

        session["user_id"] = user["id"]
        flash(f"{user['student_name']}님, 로그인되었습니다.")
        return redirect(url_for("home"))

    return render_template("login.html")


@app.route("/signup", methods=["GET", "POST"])
def signup_page():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        password_confirm = request.form.get("passwordConfirm", "").strip()

        if not username or not password or not password_confirm:
            flash("모든 항목을 입력해주세요.")
            return render_template("signup.html")

        if password != password_confirm:
            flash("비밀번호가 일치하지 않습니다.")
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

    teacher_list = get_all_teachers()

    if request.method == "POST":
        student_id = request.form.get("studentId", "").strip()
        student_name = request.form.get("studentName", "").strip()
        homeroom_teacher_id = request.form.get("homeroomTeacherId", "").strip()

        if not student_id or not student_name or not homeroom_teacher_id:
            flash("학번, 이름, 담임선생님을 모두 입력해주세요.")
            return render_template("signup_step2.html", teachers=teacher_list)

        grade, class_nm, _ = parse_student_id(student_id)
        if not grade or not class_nm:
            flash("학번 형식을 확인해주세요. 예: 2101 또는 20101")
            return render_template("signup_step2.html", teachers=teacher_list)

        if not teacher_exists(homeroom_teacher_id):
            flash("담임선생님 정보를 확인해주세요.")
            return render_template("signup_step2.html", teachers=teacher_list)

        conn = get_db()

        student_exists = conn.execute(
            "SELECT id FROM users WHERE student_id = ?",
            (student_id,)
        ).fetchone()

        if student_exists:
            conn.close()
            flash("이미 가입된 학번입니다.")
            return render_template("signup_step2.html", teachers=teacher_list)

        password_hash = generate_password_hash(signup_temp["password"])

        conn.execute(
            """
            INSERT INTO users (
                username, password_hash, student_id, student_name,
                homeroom_teacher_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                signup_temp["username"],
                password_hash,
                student_id,
                student_name,
                int(homeroom_teacher_id),
                datetime.datetime.now().isoformat()
            )
        )
        conn.commit()

        user = conn.execute(
            "SELECT id FROM users WHERE username = ?",
            (signup_temp["username"],)
        ).fetchone()
        conn.close()

        session.pop("signup_temp", None)
        session["user_id"] = user["id"]

        flash(f"{student_name}님, 회원가입이 완료되었습니다.")
        return redirect(url_for("home"))

    return render_template("signup_step2.html", teachers=teacher_list)


@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile_page():
    current_user = get_current_user()
    teacher_list = get_all_teachers()

    if request.method == "POST":
        action = request.form.get("action", "").strip()

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

        flash("올바르지 않은 요청입니다.")
        return redirect(url_for("profile_page"))

    refreshed_user = get_current_user()
    grade, class_nm, number = parse_student_id(refreshed_user["student_id"])
    grade_records = get_grade_records(refreshed_user["id"])

    return render_template(
        "profile.html",
        profile_user=refreshed_user,
        teachers=teacher_list,
        grade=grade,
        class_nm=class_nm,
        number=number,
        grade_records=grade_records
    )


@app.route("/timetable")
def timetable_page():
    grade = request.args.get("grade", "").strip()
    class_nm = request.args.get("class", "").strip()

    if not grade or not class_nm:
        current_user = get_current_user()
        if current_user and current_user["student_id"]:
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


# -------------------------
# API
# -------------------------

@app.route("/api/login-student", methods=["POST"])
def login_student():
    data = request.get_json()
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
        else:
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

    if query in ["챗봇", "학교 챗봇", "광글 챗봇"]:
        return jsonify([{
            "type": "챗봇",
            "title": "학교 정보 챗봇 페이지로 이동",
            "desc": "학교 관련 간단한 질문에 답하는 임시 챗봇입니다.",
            "link": "/chatbot"
        }])

    if query in ["프로필", "내 정보", "마이페이지", "개인정보"]:
        return jsonify([{
            "type": "프로필",
            "title": "프로필 페이지로 이동",
            "desc": "내 개인정보, 담임선생님, 학번, 내신 기록을 확인하고 수정합니다.",
            "link": "/profile"
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

    keywords.add("오늘 급식")
    keywords.add("급식")
    keywords.add("이번 주 급식")
    keywords.add("다음 주 급식")
    keywords.add("선생님 목록")
    keywords.add("선생님")
    keywords.add("챗봇")
    keywords.add("학교 챗봇")
    keywords.add("프로필")
    keywords.add("내 정보")
    keywords.add("마이페이지")
    keywords.add("학사일정")
    keywords.add("5월 일정")
    keywords.add("6월 일정")
    keywords.add("7월 일정")
    keywords.add("8월 일정")
    keywords.add("9월 일정")
    keywords.add("10월 일정")
    keywords.add("11월 일정")
    keywords.add("12월 일정")
    keywords.add("시험 일정")
    keywords.add("추석 일정")
    keywords.add("방학 일정")
    keywords.add("개학 일정")
    keywords.add("1학년 1반 시간표")
    keywords.add("2학년 1반 시간표")
    keywords.add("3학년 1반 시간표")

    query_tokens = query.split()

    suggestions = []
    for k in keywords:
        lower_k = k.lower()
        if query in lower_k or all(token in lower_k for token in query_tokens):
            suggestions.append(k)

    suggestions = sorted(set(suggestions), key=lambda x: len(x))[:8]

    return jsonify(suggestions)


# -------------------------
# 챗봇 로컬 기능 라우팅
# -------------------------

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

    if any(word in query for word in ["프로필", "내 정보", "마이페이지", "개인정보"]):
        current_user = get_current_user()
        if not current_user:
            return "프로필은 로그인 후 확인할 수 있습니다. 먼저 로그인해주세요."
        return "프로필 페이지에서 이름, 학번, 담임선생님, 비밀번호, 내신 기록을 확인하고 수정할 수 있습니다. 상단의 프로필 버튼을 눌러주세요."

    if is_calendar_query(query):
        month = extract_calendar_month(query)
        if month:
            return format_calendar_events_text(month)

        matched_events = search_calendar_events_by_keyword(query)
        if matched_events:
            return format_calendar_keyword_result(matched_events)

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

        grade, class_nm, _ = parse_student_id(current_user["student_id"])
        if not grade or not class_nm:
            return "학번에서 학년/반 정보를 해석하지 못했습니다."

        table = build_week_timetable_table(grade, class_nm)
        return format_timetable_text(f"{grade}학년 {class_nm}반 이번 주 시간표", table)

    grade, class_nm = extract_grade_class(query)
    if grade and class_nm and "시간표" in query:
        table = build_week_timetable_table(grade, class_nm)
        return format_timetable_text(f"{grade}학년 {class_nm}반 이번 주 시간표", table)

    keywords = [
        "선생님", "교사", "학생부", "보건실", "행정실", "교무실",
        "방송실", "도서관", "시설", "위치", "목록"
    ]
    if any(keyword in query for keyword in keywords):
        local_answer = build_local_search_answer(query)
        if local_answer:
            return local_answer

    return None


@app.route("/api/chat", methods=["POST"])
def chat_api():
    data = request.get_json()
    user_message = data.get("message", "").strip()

    if not user_message:
        return jsonify({
            "success": False,
            "reply": "질문을 입력해주세요."
        }), 400

    local_reply = handle_chat_locally(user_message)
    if local_reply:
        return jsonify({
            "success": True,
            "reply": local_reply
        })

    if not OPENAI_API_KEY or not openai_client:
        return jsonify({
            "success": False,
            "reply": "현재 챗봇 API가 설정되지 않았고, 로컬에서도 답을 찾지 못했습니다."
        }), 500

    if not OPENAI_MODEL:
        return jsonify({
            "success": False,
            "reply": "OPENAI_MODEL이 설정되지 않았습니다."
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
            f"학번={current_user['student_id']}, "
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


if __name__ == "__main__":
    app.run(debug=True)