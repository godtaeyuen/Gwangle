from flask import Flask, render_template, request, jsonify
import json
import os
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


def get_menus(school_name, office_education, day=None):
    if day is None:
        day = datetime.date.today().strftime("%Y%m%d")

    params = {
        "KEY": SERVICE_KEY,
        "Type": "json",
    }

    info_url = BASE_URL + "schoolInfo"

    params.update({"SCHUL_NM": school_name})
    params.update({"MLSV_YMD": day})

    response = requests.get(info_url, params=params, timeout=15)
    school_data = response.json()

    if "schoolInfo" not in school_data:
        return []

    school_rows = school_data["schoolInfo"][1]["row"]

    found_school = None
    for schoolinfo in school_rows:
        if schoolinfo["ATPT_OFCDC_SC_NM"] == office_education:
            found_school = schoolinfo
            break

    if not found_school:
        return []

    params.update({
        "ATPT_OFCDC_SC_CODE": found_school["ATPT_OFCDC_SC_CODE"],
        "SD_SCHUL_CODE": found_school["SD_SCHUL_CODE"]
    })

    response = requests.get(BASE_URL + "mealServiceDietInfo", params=params, timeout=15)
    meal_data = response.json()

    if "mealServiceDietInfo" not in meal_data:
        return []

    raw_menu = meal_data["mealServiceDietInfo"][1]["row"][0]["DDISH_NM"]
    menu_list = []

    for item in raw_menu.split("<br/>"):
        cleaned = item.replace("#", "").strip()

        while cleaned and (cleaned[-1].isdigit() or cleaned[-1] == "."):
            cleaned = cleaned[:-1].strip()

        if cleaned:
            menu_list.append(cleaned)

    return menu_list


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/search")
def search():
    query = request.args.get("query", "").strip().lower()

    if query in ["오늘 급식", "급식", "오늘급식"]:
        menus = get_menus("광명고등학교", "경기도교육청")

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

    results = []
    for item in search_data:
        blob = build_blob(item)
        if query and query in blob:
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

    suggestions = [k for k in keywords if query and query in k.lower()]
    suggestions = sorted(suggestions, key=lambda x: len(x))[:8]

    return jsonify(suggestions)


if __name__ == "__main__":
    app.run(debug=True)