from pathlib import Path
import re
import shutil
import datetime

BASE_DIR = Path(__file__).resolve().parent
APP_PATH = BASE_DIR / "app.py"
PROMO_REQUEST_PATH = BASE_DIR / "templates" / "promo_request.html"

def backup(path: Path, backup_dir: Path):
    if path.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_dir / path.name)

def patch_app_py():
    text = APP_PATH.read_text(encoding="utf-8")

    # 전체 요청 용량 제한: 파일 20MB + multipart 여유분
    text = re.sub(
        r"MAX_CONTENT_LENGTH\s*=\s*.*",
        "MAX_CONTENT_LENGTH = 25 * 1024 * 1024",
        text,
        count=1
    )

    # 홍보 이미지 제한: 20MB
    text = re.sub(
        r"PROMO_MAX_IMAGE_BYTES\s*=\s*.*",
        "PROMO_MAX_IMAGE_BYTES = 20 * 1024 * 1024",
        text
    )

    text = text.replace(
        "홍보물 이미지는 5MB 이하로 업로드해주세요.",
        "홍보물 이미지는 20MB 이하로 업로드해주세요."
    )

    text = text.replace(
        "이미지는 5MB 이하의 png, jpg, jpeg, webp, gif 파일만 가능합니다.",
        "이미지는 20MB 이하의 png, jpg, jpeg, webp, gif 파일만 가능합니다."
    )

    APP_PATH.write_text(text, encoding="utf-8")

def patch_promo_request_html():
    if not PROMO_REQUEST_PATH.exists():
        print("⚠️ templates/promo_request.html 파일을 찾지 못했습니다.")
        return

    text = PROMO_REQUEST_PATH.read_text(encoding="utf-8")

    text = text.replace("5MB", "20MB")

    text = text.replace(
        "const MAX_IMAGE_SIZE = 5 * 1024 * 1024;",
        "const MAX_IMAGE_SIZE = 20 * 1024 * 1024;"
    )

    text = text.replace(
        "file.size > 5 * 1024 * 1024",
        "file.size > 20 * 1024 * 1024"
    )

    text = text.replace(
        "이미지는 5MB 이하만 업로드할 수 있습니다.",
        "이미지는 20MB 이하만 업로드할 수 있습니다."
    )

    text = text.replace(
        "홍보물 이미지는 5MB 이하만 업로드할 수 있습니다.",
        "홍보물 이미지는 20MB 이하만 업로드할 수 있습니다."
    )

    PROMO_REQUEST_PATH.write_text(text, encoding="utf-8")

def main():
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BASE_DIR / f"backup_before_20mb_{timestamp}"

    backup(APP_PATH, backup_dir)
    backup(PROMO_REQUEST_PATH, backup_dir)

    patch_app_py()
    patch_promo_request_html()

    print("✅ 홍보물 파일 제한을 20MB 이하로 변경했습니다.")
    print("✅ 서버 전체 요청 제한은 25MB로 설정했습니다.")
    print(f"✅ 백업 폴더: {backup_dir}")

if __name__ == "__main__":
    main()