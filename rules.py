"""
이상 판단 규칙
--------------
시뮬레이터(수집 서버 역할)와 관제 화면이 같은 기준을 쓰도록 판단 로직을 한곳에 모았습니다.
기준 숫자는 config.py 에서 바꿉니다.
"""
from datetime import datetime

import pandas as pd

import config

# 이벤트(알림) 종류 이름
EV_PRESSURE = "압력 이상"
EV_HAMMER = "수충격 의심"
EV_COMM_LOST = "통신 두절"
EV_COMM_BACK = "통신 복구"

# 센서 상태 이름
ST_NORMAL = "정상"
ST_WARNING = "경고"
ST_OFFLINE = "통신 두절"


def is_out_of_range(pressure: float) -> bool:
    """압력이 정상 범위를 벗어났는지"""
    return pressure < config.NORMAL_MIN or pressure > config.NORMAL_MAX


def is_water_hammer(prev_pressure, prev_time: datetime, pressure: float, time: datetime) -> bool:
    """바로 앞 값과 비교해 짧은 시간에 압력이 크게 변했는지 (수충격 의심)"""
    if prev_pressure is None or prev_time is None:
        return False
    gap = (time - prev_time).total_seconds()
    if gap <= 0 or gap > config.HAMMER_MAX_GAP_SEC:
        return False  # 데이터가 비어 있던 구간 사이는 비교하지 않음
    return abs(pressure - prev_pressure) >= config.HAMMER_DELTA


def is_offline(last_received: datetime | None, now: datetime) -> bool:
    """마지막으로 데이터를 받은 지 기준 시간을 넘겼는지"""
    if last_received is None or pd.isna(last_received):
        return True
    return (now - last_received).total_seconds() > config.COMM_TIMEOUT_SEC


# ───────────────────── 화면용 판단 (표 단위로 한꺼번에) ─────────────────────

def mark_anomalies(df: pd.DataFrame) -> pd.DataFrame:
    """측정값 표에 '압력 이상', '수충격 의심' 여부 열을 붙입니다. (센서별·시각순 정렬 가정)"""
    df = df.sort_values(["sensor_id", "measured_at"]).copy()
    df["out_of_range"] = (df["pressure"] < config.NORMAL_MIN) | (df["pressure"] > config.NORMAL_MAX)
    g = df.groupby("sensor_id")
    gap = g["measured_at"].diff().dt.total_seconds()
    jump = g["pressure"].diff().abs()
    df["hammer"] = (gap > 0) & (gap <= config.HAMMER_MAX_GAP_SEC) & (jump >= config.HAMMER_DELTA)
    return df


def compute_status(latest: pd.DataFrame, recent: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """
    센서별 현재 상태를 정합니다. 우선순위: 통신 두절 > 경고 > 정상
      - 통신 두절: 마지막 수신이 COMM_TIMEOUT_SEC 초보다 오래됨
      - 경고    : 최근 WARNING_HOLD_SEC 초 안에 압력 이상 또는 수충격 의심 값이 있음
    latest: db.read_latest() 결과, recent: 최근 측정값 표
    """
    out = latest.copy()
    reasons = {}
    if not recent.empty:
        marked = mark_anomalies(recent)
        since = now - pd.Timedelta(seconds=config.WARNING_HOLD_SEC)
        hot = marked[(marked["measured_at"] >= since) & (marked["out_of_range"] | marked["hammer"])]
        for sid, rows in hot.groupby("sensor_id"):
            r = []
            if rows["out_of_range"].any():
                r.append(EV_PRESSURE)
            if rows["hammer"].any():
                r.append(EV_HAMMER)
            reasons[sid] = ", ".join(r)

    status, detail = [], []
    for _, row in out.iterrows():
        if is_offline(row["last_received"], now):
            status.append(ST_OFFLINE)
            detail.append("데이터 수신 없음" if pd.isna(row["last_received"])
                          else f"{int((now - row['last_received']).total_seconds())}초째 수신 없음")
        elif row["sensor_id"] in reasons:
            status.append(ST_WARNING)
            detail.append(reasons[row["sensor_id"]])
        else:
            status.append(ST_NORMAL)
            detail.append("")
    out["status"] = status
    out["detail"] = detail
    return out


def downsample(df: pd.DataFrame, bucket_sec: int) -> pd.DataFrame:
    """
    긴 기간을 볼 때 점이 너무 많지 않도록 bucket_sec 초 단위 평균으로 줄입니다.
    데이터가 하나도 없는 칸은 빈 값(NaN)으로 남아 그래프에서 끊겨 보입니다.
    (센서 1개 분량의 표를 넣어 주세요)
    """
    if df.empty or bucket_sec <= 1:
        return df
    out = (df.set_index("measured_at")["pressure"]
             .resample(f"{int(bucket_sec)}s").mean()
             .reset_index())
    out["sensor_id"] = df["sensor_id"].iloc[0]
    return out


def insert_gaps(df: pd.DataFrame, gap_sec: float) -> pd.DataFrame:
    """
    데이터가 gap_sec 초 넘게 빈 곳에 '빈 값(NaN)' 행을 끼워 넣습니다.
    그래프는 빈 값에서 선을 끊으므로, 빠진 구간이 이어 붙지 않고 빈칸으로 보입니다.
    (센서 1개 분량, 시각순 정렬된 표를 넣어 주세요)
    """
    if df.empty:
        return df
    df = df.sort_values("measured_at")
    gaps = df["measured_at"].diff().dt.total_seconds() > gap_sec
    if not gaps.any():
        return df
    breaks = df.loc[gaps, ["measured_at"]].copy()
    breaks["measured_at"] = breaks["measured_at"] - pd.Timedelta(milliseconds=500)
    breaks["pressure"] = float("nan")
    return pd.concat([df, breaks], ignore_index=True).sort_values("measured_at", kind="stable")
