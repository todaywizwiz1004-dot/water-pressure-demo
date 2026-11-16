"""
데이터베이스(SQLite) 다루기
--------------------------
- 파일 하나(water_demo.db)에 모든 데이터를 저장합니다. 별도 설치가 필요 없습니다.
- 시뮬레이터는 쓰기, 관제 화면은 읽기를 동시에 하므로 WAL 모드를 켭니다.

표(테이블) 구성
  sensors   : 센서 목록 (ID, 이름, 위도, 경도)
  readings  : 압력 측정값. (센서 ID + 측정 시각)이 같으면 한 번만 저장됩니다.
              measured_at = 센서가 잰 시각, received_at = 서버가 받은 시각
  events    : 알림 기록 (압력 이상, 수충격 의심, 통신 두절, 통신 복구)
  comm_state: 센서별 현재 통신 상태 (두절/복구 알림을 한 번씩만 남기기 위해 사용)
"""
import sqlite3
from datetime import datetime

import pandas as pd

import config

TIME_FMT = "%Y-%m-%d %H:%M:%S"


def to_text(t: datetime) -> str:
    return t.strftime(TIME_FMT)


def to_time(s: str) -> datetime:
    return datetime.strptime(s, TIME_FMT)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")      # 읽기·쓰기 동시 사용
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """표가 없으면 만들고, 센서 목록을 채웁니다."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sensors (
            sensor_id TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            lat       REAL NOT NULL,
            lon       REAL NOT NULL
        );
        -- PRIMARY KEY(센서 ID, 측정 시각) 덕분에 같은 데이터는 두 번 저장되지 않습니다.
        CREATE TABLE IF NOT EXISTS readings (
            sensor_id   TEXT NOT NULL,
            measured_at TEXT NOT NULL,
            pressure    REAL NOT NULL,
            received_at TEXT NOT NULL,
            PRIMARY KEY (sensor_id, measured_at)
        );
        CREATE INDEX IF NOT EXISTS idx_readings_time ON readings(measured_at);
        CREATE INDEX IF NOT EXISTS idx_readings_recv ON readings(sensor_id, received_at);

        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time  TEXT NOT NULL,   -- 일이 생긴 시각 (측정 시각 기준)
            detected_at TEXT NOT NULL,   -- 서버가 알아챈 시각
            sensor_id   TEXT NOT NULL,
            event_type  TEXT NOT NULL,
            message     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time);

        CREATE TABLE IF NOT EXISTS comm_state (
            sensor_id  TEXT PRIMARY KEY,
            is_offline INTEGER NOT NULL DEFAULT 0
        );

        -- 시연용 명령 (화면에서 버튼을 누르면 시뮬레이터가 읽어서 실행)
        CREATE TABLE IF NOT EXISTS commands (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            sensor_id  TEXT NOT NULL,
            command    TEXT NOT NULL,     -- 'hammer'(수충격) 또는 'outage'(통신 끊기)
            created_at TEXT NOT NULL,
            done       INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.executemany(
        "INSERT OR REPLACE INTO sensors(sensor_id, name, lat, lon) VALUES (:sensor_id, :name, :lat, :lon)",
        config.SENSORS,
    )
    conn.commit()


# ───────────────────── 쓰기 (시뮬레이터 쪽) ─────────────────────

def insert_reading(conn, sensor_id: str, measured_at: datetime, pressure: float, received_at: datetime) -> bool:
    """측정값 1건 저장. 이미 같은 (센서 ID, 측정 시각)이 있으면 무시하고 False 를 돌려줍니다."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO readings(sensor_id, measured_at, pressure, received_at) VALUES (?, ?, ?, ?)",
        (sensor_id, to_text(measured_at), round(pressure, 3), to_text(received_at)),
    )
    return cur.rowcount == 1


def previous_reading(conn, sensor_id: str, measured_at: datetime):
    """주어진 시각 바로 앞의 측정값 (수충격 판단용)"""
    row = conn.execute(
        "SELECT measured_at, pressure FROM readings WHERE sensor_id=? AND measured_at<? "
        "ORDER BY measured_at DESC LIMIT 1",
        (sensor_id, to_text(measured_at)),
    ).fetchone()
    return (to_time(row[0]), row[1]) if row else (None, None)


def add_event(conn, event_time: datetime, detected_at: datetime, sensor_id: str, event_type: str, message: str):
    conn.execute(
        "INSERT INTO events(event_time, detected_at, sensor_id, event_type, message) VALUES (?, ?, ?, ?, ?)",
        (to_text(event_time), to_text(detected_at), sensor_id, event_type, message),
    )


def last_received(conn) -> dict:
    """센서별 마지막 수신 시각"""
    rows = conn.execute("SELECT sensor_id, MAX(received_at) FROM readings GROUP BY sensor_id").fetchall()
    return {sid: to_time(t) for sid, t in rows if t}


def get_comm_state(conn) -> dict:
    return dict(conn.execute("SELECT sensor_id, is_offline FROM comm_state").fetchall())


def set_comm_state(conn, sensor_id: str, offline: bool):
    conn.execute(
        "INSERT INTO comm_state(sensor_id, is_offline) VALUES (?, ?) "
        "ON CONFLICT(sensor_id) DO UPDATE SET is_offline=excluded.is_offline",
        (sensor_id, int(offline)),
    )


def add_command(conn, sensor_id: str, command: str):
    """화면 → 시뮬레이터 시연 명령 넣기"""
    conn.execute("INSERT INTO commands(sensor_id, command, created_at) VALUES (?, ?, ?)",
                 (sensor_id, command, to_text(config.now())))
    conn.commit()


def take_commands(conn) -> list:
    """아직 처리 안 된 시연 명령을 꺼내고 '처리됨'으로 표시"""
    rows = conn.execute("SELECT id, sensor_id, command FROM commands WHERE done=0 ORDER BY id").fetchall()
    if rows:
        conn.executemany("UPDATE commands SET done=1 WHERE id=?", [(r[0],) for r in rows])
        conn.commit()
    return [(sid, cmd) for _, sid, cmd in rows]


# ───────────────────── 읽기 (관제 화면 쪽) ─────────────────────

def read_sensors(conn) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM sensors ORDER BY sensor_id", conn)


def read_readings(conn, start: datetime, end: datetime, sensor_ids=None) -> pd.DataFrame:
    """기간 안의 측정값 (측정 시각 기준)"""
    sql = "SELECT sensor_id, measured_at, pressure, received_at FROM readings WHERE measured_at BETWEEN ? AND ?"
    params = [to_text(start), to_text(end)]
    if sensor_ids:
        sql += f" AND sensor_id IN ({','.join('?' * len(sensor_ids))})"
        params += list(sensor_ids)
    sql += " ORDER BY sensor_id, measured_at"
    df = pd.read_sql_query(sql, conn, params=params)
    df["measured_at"] = pd.to_datetime(df["measured_at"], format=TIME_FMT)
    df["received_at"] = pd.to_datetime(df["received_at"], format=TIME_FMT)
    return df


def read_latest(conn) -> pd.DataFrame:
    """센서별 가장 최근 측정값과 마지막 수신 시각"""
    df = pd.read_sql_query(
        """
        SELECT s.sensor_id, s.name, s.lat, s.lon,
               r.pressure     AS last_pressure,
               r.measured_at  AS last_measured,
               (SELECT MAX(received_at) FROM readings WHERE sensor_id = s.sensor_id) AS last_received
        FROM sensors s
        LEFT JOIN readings r
          ON r.sensor_id = s.sensor_id
         AND r.measured_at = (SELECT MAX(measured_at) FROM readings WHERE sensor_id = s.sensor_id)
        ORDER BY s.sensor_id
        """,
        conn,
    )
    for c in ("last_measured", "last_received"):
        df[c] = pd.to_datetime(df[c], format=TIME_FMT)
    df["last_pressure"] = pd.to_numeric(df["last_pressure"])  # 데이터가 없어도 숫자 열로 유지
    return df


def read_events(conn, limit: int = 50, start: datetime = None, end: datetime = None) -> pd.DataFrame:
    sql = (
        "SELECT e.id, e.event_time, e.detected_at, e.sensor_id, s.name, e.event_type, e.message "
        "FROM events e JOIN sensors s ON s.sensor_id = e.sensor_id"
    )
    params = []
    if start and end:
        sql += " WHERE e.event_time BETWEEN ? AND ?"
        params = [to_text(start), to_text(end)]
    sql += " ORDER BY e.detected_at DESC, e.id DESC LIMIT ?"
    params.append(limit)
    df = pd.read_sql_query(sql, conn, params=params)
    for c in ("event_time", "detected_at"):
        df[c] = pd.to_datetime(df[c], format=TIME_FMT)
    return df
