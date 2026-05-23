import sqlite3

DB_PATH = "gwangle.db"


def print_all_tables(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT name
        FROM sqlite_master
        WHERE type='table'
        ORDER BY name
    """)
    tables = [row[0] for row in cursor.fetchall()]

    print("=== 테이블 목록 ===")
    if not tables:
        print("테이블이 없습니다.")
    else:
        for table in tables:
            print("-", table)
    print()

    return tables


def print_table_schema(conn: sqlite3.Connection, table_name: str) -> None:
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()

    print(f"=== {table_name} 컬럼 구조 ===")
    if not columns:
        print("컬럼 정보가 없습니다.")
    else:
        for col in columns:
            cid, name, col_type, notnull, default_value, pk = col
            print(
                f"- 이름: {name}, 타입: {col_type}, "
                f"NOT NULL: {notnull}, 기본값: {default_value}, PK: {pk}"
            )
    print()


def print_table_rows(conn: sqlite3.Connection, table_name: str) -> None:
    cursor = conn.cursor()

    try:
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()
        column_names = [description[0] for description in cursor.description]
    except sqlite3.Error as e:
        print(f"{table_name} 조회 중 오류:", e)
        print()
        return

    print(f"=== {table_name} 데이터 ===")
    if not rows:
        print("데이터가 없습니다.")
        print()
        return

    print("컬럼:", ", ".join(column_names))
    for idx, row in enumerate(rows, start=1):
        print(f"[{idx}] {tuple(row)}")
    print()


def main() -> None:
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error as e:
        print("DB 연결 실패:", e)
        return

    try:
        tables = print_all_tables(conn)

        for table in tables:
            print_table_schema(conn, table)
            print_table_rows(conn, table)

    finally:
        conn.close()


if __name__ == "__main__":
    main()