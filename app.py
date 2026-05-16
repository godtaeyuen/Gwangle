from flask import Flask, render_template, request, jsonify
import json
import os
import requests
import datetime
from dotenv import load_dotenv

# .env 파일에서 인증키 불러오기
load_dotenv()

app = Flask(__name__)

# NEIS 인증키
SERVICE_KEY = os.getenv("EDU_API_KEY")

# NEIS 기본 주소
BASE_URL = "https://open.neis.go.kr/hub/"

# 로컬 JSON 데이터 불러오기
with open("data/teachers.json", "r", encoding="utf-8") as f:
    teachers = json.load(f)

with open("data/facilities.json", "r", encoding="utf-8") as f:
    facilities = json.load(f)

search_data = teachers + facilities


# 검색용 문자열 합치기
def build_blob(item):
    fields = [item.get("title", ""), item.get("desc", "")]
    fields += item.get("keywords", [])
    fields += item.get("tags", [])
    return " ".join(fields).lower()


# 검색 정확도 보강
def is_match(query, item):
    blob = build_blob(item)
    query = query.lower().strip()

    if not query:
        return False

    # 전체 문장 포함
    if query in blob:
        return True

    # 띄어쓰기 단위로 전부 포함
    query_tokens = query.split()
    if all(token in blob for token in query_tokens):
        return True

    return False


# 급식 문자열 정리
def clean_menu_list(raw_menu):
    menu_list = []

    for item in raw_menu.split("<br/>"):
        cleaned = item.replace("#", "").strip()

        # 뒤의 알레르기 숫자 제거
        while cleaned and (cleaned[-1].isdigit() or cleaned[-1] == "."):
            cleaned = cleaned[:-1].strip()

        if cleaned:
            menu_list.append(cleaned)

    return menu_list


# 학교 코드 찾기
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


# 하루 급식 가져오기
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


# 이번주/다음주 월~금 날짜 구하기
def get_week_dates(offset_weeks=0):
    today = datetime.date.today()
    monday = today - datetime.timedelta(days=today.weekday()) + datetime.timedelta(weeks=offset_weeks)

    days = []
    for i in range(5):  # 월~금
        current_day = monday + datetime.timedelta(days=i)
        days.append(current_day)

    return days


# 이번주/다음주 급식 가져오기
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


@app.route("/")
def home():
    return render_template("index.html")


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

    # 급식 추천어 추가
    keywords.add("오늘 급식")
    keywords.add("급식")
    keywords.add("이번주 급식")
    keywords.add("이번 주 급식")
    keywords.add("다음주 급식")
    keywords.add("다음 주 급식")

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