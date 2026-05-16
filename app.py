from flask import Flask, render_template, request, jsonify
import json
import os
import re
import requests
import datetime
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

SERVICE_KEY = os.getenv("EDU_API_KEY")
BASE_URL = "https://open.neis.go.kr/hub/"

with open("data/teachers.json", "r", encoding="utf-8") as f:
    teachers = json.load(f)

with open("data/facilities.json", "r", encoding="utf-8") as f:
    facilities = json.load(f)

search_data = teachers + facilities


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


# -------------------------
# 시간표 관련
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
    """
    학번을 학년/반/번호로 해석
    지원 형식:
    - 4자리: 3618 -> 3학년 6반 18번
    - 5자리: 30618 -> 3학년 6반 18번
    """
    student_id = re.sub(r"\D", "", student_id)

    if len(student_id) == 4:
        grade = student_id[0]
        class_nm = student_id[1]
        number = student_id[2:]
        return grade, class_nm, number

    if len(student_id) == 5:
        grade = student_id[0]
        class_nm = str(int(student_id[1:3]))  # 06 -> 6
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


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/timetable")
def timetable_page():
    return render_template("timetable.html")


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


@app.route("/api/search")
def search():
    query = request.args.get("query", "").strip().lower()

    # 오늘 급식
    if query in ["오늘 급식", "급식", "오늘급식"]:
        menus = get_menus_by_day(
            "광명고등학교",
            "경기도교육청",
            datetime.date.today().strftime("%Y%m%d")
        )

        if menus:
            return jsonify([{
                "type": "급식 정보",
                "title": "오늘의 급식",
                "desc": "광명고등학교 오늘 급식 메뉴입니다.",
                "tags": menus
            }])
        else:
            return jsonify([{
                "type": "급식 정보",
                "title": "오늘의 급식",
                "desc": "급식 정보를 불러오지 못했습니다.",
                "tags": []
            }])

    # 이번주 급식
    if query in ["이번주 급식", "이번 주 급식"]:
        week_meals = get_week_meals("광명고등학교", "경기도교육청", offset_weeks=0)

        results = []
        for day_info in week_meals:
            results.append({
                "type": "주간 급식 정보",
                "title": f"{day_info['date']} ({day_info['weekday']})",
                "desc": "광명고등학교 이번 주 급식",
                "tags": day_info["menus"]
            })

        return jsonify(results)

    # 다음주 급식
    if query in ["다음주 급식", "다음 주 급식"]:
        week_meals = get_week_meals("광명고등학교", "경기도교육청", offset_weeks=1)

        results = []
        for day_info in week_meals:
            results.append({
                "type": "주간 급식 정보",
                "title": f"{day_info['date']} ({day_info['weekday']})",
                "desc": "광명고등학교 다음 주 급식",
                "tags": day_info["menus"]
            })

        return jsonify(results)

    # 검색으로 시간표
    grade, class_nm = extract_grade_class(query)
    if grade and class_nm and "시간표" in query:
        return jsonify([{
            "type": "시간표 정보",
            "title": f"{grade}학년 {class_nm}반 시간표",
            "desc": "시간표 페이지로 이동합니다.",
            "link": f"/timetable?grade={grade}&class={class_nm}"
        }])

    # 일반 검색
    results = []
    for item in search_data:
        if is_match(query, item):
            results.append(item)

    return jsonify(results)


@app.route("/api/suggest")
def suggest():
    query = request.args.get("query", "").strip().lower()

    keywords = set()
    for item in search_data:
        title = item.get("title", "")
        if title:
            keywords.add(title)

        for keyword in item.get("keywords", []):
            keywords.add(keyword)

    keywords.add("오늘 급식")
    keywords.add("급식")
    keywords.add("이번주 급식")
    keywords.add("이번 주 급식")
    keywords.add("다음주 급식")
    keywords.add("다음 주 급식")
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


if __name__ == "__main__":
    app.run(debug=True)