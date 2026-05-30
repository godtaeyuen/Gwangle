import time
import threading
from collections import defaultdict, deque
from flask import request, jsonify, session


MAX_CONTENT_LENGTH = 128 * 1024

RATE_LIMITS = {
    "/api/chat": (12, 60),
    "/api/search": (90, 60),
    "/api/suggest": (90, 60),
    "/login": (20, 300),
    "/signup": (20, 300),
    "/signup/step2": (20, 300),
    "/profile": (30, 300),
}

DEFAULT_POST_LIMIT = (60, 60)

FIELD_LIMITS = {
    "message": 600,
    "query": 100,

    "username": 40,
    "password": 100,
    "passwordConfirm": 100,
    "currentPassword": 100,
    "newPassword": 100,
    "newPasswordConfirm": 100,

    "studentName": 20,
    "studentId": 10,
    "homeroomTeacherId": 10,

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
}

DEFAULT_FIELD_LIMIT = 300

_request_log = defaultdict(deque)
_lock = threading.Lock()


def _error_response(message, status_code):
    if request.path.startswith("/api/"):
        return jsonify({
            "success": False,
            "message": message,
            "reply": message
        }), status_code

    return message, status_code


def _client_key():
    ip = request.remote_addr or "unknown"
    user_id = session.get("user_id", "guest")
    return f"{ip}:{user_id}:{request.method}:{request.path}"


def _is_rate_limited(key, limit, window_seconds):
    now = time.time()

    with _lock:
        q = _request_log[key]

        while q and now - q[0] > window_seconds:
            q.popleft()

        if len(q) >= limit:
            return True

        q.append(now)
        return False


def _has_bad_control_chars(text):
    for ch in text:
        code = ord(ch)

        if code < 32 and ch not in ["\n", "\r", "\t"]:
            return True

    return False


def _has_extreme_repetition(text):
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


def _validate_text(field_name, value):
    if value is None:
        return None

    if not isinstance(value, str):
        value = str(value)

    limit = FIELD_LIMITS.get(field_name, DEFAULT_FIELD_LIMIT)

    if len(value) > limit:
        return f"'{field_name}' 입력이 너무 깁니다. 최대 {limit}자까지 입력할 수 있습니다."

    if _has_bad_control_chars(value):
        return f"'{field_name}' 입력에 허용되지 않는 문자가 포함되어 있습니다."

    if _has_extreme_repetition(value):
        return f"'{field_name}' 입력에 비정상적으로 반복되는 문자가 너무 많습니다."

    return None


def _walk_json(data, parent_key=""):
    if isinstance(data, dict):
        for key, value in data.items():
            field_name = str(key)
            error = _walk_json(value, field_name)
            if error:
                return error

    elif isinstance(data, list):
        if len(data) > 50:
            return "한 번에 너무 많은 데이터를 보낼 수 없습니다."

        for item in data:
            error = _walk_json(item, parent_key)
            if error:
                return error

    elif isinstance(data, str):
        return _validate_text(parent_key, data)

    return None


def _validate_request_inputs():
    for key in request.args:
        for value in request.args.getlist(key):
            error = _validate_text(key, value)
            if error:
                return error

    for key in request.form:
        values = request.form.getlist(key)

        if len(values) > 20:
            return f"'{key}' 값이 너무 많이 전송되었습니다."

        for value in values:
            error = _validate_text(key, value)
            if error:
                return error

    if request.is_json:
        data = request.get_json(silent=True)

        if data is None:
            return "JSON 요청 형식이 올바르지 않습니다."

        error = _walk_json(data)
        if error:
            return error

    return None


def apply_security(app):
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

    @app.before_request
    def security_guard():
        content_length = request.content_length

        if content_length is not None and content_length > MAX_CONTENT_LENGTH:
            return _error_response("요청 데이터가 너무 큽니다.", 413)

        if request.method in ["POST", "PUT", "PATCH"]:
            error = _validate_request_inputs()
            if error:
                return _error_response(error, 400)

        if request.path in RATE_LIMITS:
            limit, window = RATE_LIMITS[request.path]
        elif request.method in ["POST", "PUT", "PATCH"]:
            limit, window = DEFAULT_POST_LIMIT
        else:
            return None

        key = _client_key()

        if _is_rate_limited(key, limit, window):
            return _error_response(
                "요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
                429
            )

        return None

    @app.errorhandler(413)
    def too_large(error):
        return _error_response("요청 데이터가 너무 큽니다.", 413)