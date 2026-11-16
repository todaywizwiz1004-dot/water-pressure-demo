"""
상수도 관망 압력 관제 화면 (Streamlit)
=====================================
실행:  streamlit run app.py
먼저 다른 창에서 simulator.py 를 켜 두어야 데이터가 들어옵니다.
"""
import os
import threading
import traceback
from datetime import datetime, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import db
import rules
import simulator

# 상태별 색깔 (정상 초록, 경고 빨강, 통신 두절 회색)
STATUS_COLOR = {
    rules.ST_NORMAL: "#2E9E5B",
    rules.ST_WARNING: "#D64545",
    rules.ST_OFFLINE: "#9AA0A6",
}

st.set_page_config(page_title="상수도 관망 압력 관제 데모", page_icon="💧", layout="wide")



# ═══════════════ 시뮬레이터 함께 돌리기 (인터넷 배포용) ═══════════════
# Streamlit Cloud 같은 인터넷 서버에서는 이 화면(app.py)만 실행되므로,
# 따로 켜진 시뮬레이터가 없으면 화면 안에서 시뮬레이터를 백그라운드로 돌립니다.
# run.bat 으로 켤 때는 시뮬레이터가 별도 창에서 돌기 때문에 이 기능을 끕니다.
@st.cache_resource  # 서버 전체에서 딱 한 번만 실행 (접속자가 여러 명이어도 시뮬레이터는 1개)
def start_embedded_simulator():
    backfill = 0 if simulator.has_any_data() else 30  # 처음이면 지난 30분 데이터를 채워 둠

    def worker():
        try:
            simulator.run(backfill_min=backfill)
        except Exception:
            traceback.print_exc()

    thread = threading.Thread(target=worker, daemon=True, name="embedded-simulator")
    thread.start()
    return thread


if os.environ.get("WATER_DEMO_EXTERNAL_SIM") != "1" and not simulator.is_already_running():
    start_embedded_simulator()

# 지도 중심은 고정값 (매번 계산하면 미세하게 달라져 지도가 다시 그려질 수 있음)
MAP_CENTER = dict(
    lat=round(sum(s["lat"] for s in config.SENSORS) / len(config.SENSORS), 4),
    lon=round(sum(s["lon"] for s in config.SENSORS) / len(config.SENSORS), 4),
)

# 자동 새로고침 중 화면이 잠깐 흐려지는(깜박이는) 효과 끄기
st.markdown(
    """
    <style>
    .stale-element, [data-stale="true"] { opacity: 1 !important; transition: none !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


def open_db():
    """화면이 새로고침될 때마다 DB에 새로 연결합니다 (읽기 전용 용도)."""
    conn = db.connect()
    db.init_db(conn)  # 시뮬레이터보다 화면을 먼저 켜도 오류가 나지 않도록 표를 준비
    return conn


def load_current_status(conn, now: datetime):
    """지금 시각 기준 센서별 상태 표"""
    latest = db.read_latest(conn)
    recent = db.read_readings(conn, now - timedelta(seconds=config.WARNING_HOLD_SEC + 2), now)
    return rules.compute_status(latest, recent, now)


# ═══════════════ 2단계: 상태 카드 ═══════════════
def draw_status_cards(status_df):
    counts = status_df["status"].value_counts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("전체 센서", f"{len(status_df)}개")
    c2.metric("🟢 정상", f"{counts.get(rules.ST_NORMAL, 0)}개")
    c3.metric("🔴 경고 (압력 이상)", f"{counts.get(rules.ST_WARNING, 0)}개")
    c4.metric("⚪ 통신 두절", f"{counts.get(rules.ST_OFFLINE, 0)}개")


# ═══════════════ 2단계: 센서 위치 지도 ═══════════════
def draw_map(status_df):
    fig = go.Figure()
    # 상태별로 점을 따로 그려야 범례(색깔 설명)가 생깁니다
    # 깜박임 방지: 지도에는 '상태가 바뀔 때만 달라지는 내용'만 넣습니다.
    # (매초 바뀌는 압력 숫자·경과 시간은 옆 표에 표시) → 상태가 그대로면 지도를 다시 그리지 않음
    for status, color in STATUS_COLOR.items():
        part = status_df[status_df["status"] == status]  # 비어 있어도 그려서 범례를 항상 3색으로 유지
        hover = [
            f"<b>{r['name']}</b> ({r['sensor_id']})<br>상태: {r['status']}"
            + (f" - {r['detail']}" if r["status"] == rules.ST_WARNING else "")
            for _, r in part.iterrows()
        ]
        fig.add_trace(go.Scattermap(
            lat=part["lat"].tolist(), lon=part["lon"].tolist(), mode="markers", name=status,
            marker=dict(size=20, color=color, opacity=0.9),
            hovertext=hover, hoverinfo="text",
        ))
    fig.update_layout(
        map=dict(style="carto-positron", zoom=12, center=MAP_CENTER),
        uirevision="sensor_map",  # 새로고침돼도 사용자가 옮긴 지도 위치·확대를 유지
        height=430, margin=dict(l=0, r=0, t=0, b=0),
        legend=dict(orientation="h", yanchor="bottom", y=0.01, xanchor="left", x=0.01,
                    bgcolor="rgba(255,255,255,0.8)"),
    )
    st.plotly_chart(fig, key="sensor_map")


def draw_sensor_table(status_df):
    """지도 옆 센서 상태 표"""
    icon = {rules.ST_NORMAL: "🟢", rules.ST_WARNING: "🔴", rules.ST_OFFLINE: "⚪"}
    table = status_df.assign(
        상태=status_df["status"].map(lambda s: f"{icon[s]} {s}"),
        **{f"압력({config.PRESSURE_UNIT})": status_df["last_pressure"].round(2)},
        마지막수신=status_df["last_received"].dt.strftime("%H:%M:%S"),
    )[["sensor_id", "name", "상태", f"압력({config.PRESSURE_UNIT})", "마지막수신", "detail"]]
    table.columns = ["ID", "센서 이름", "상태", f"압력({config.PRESSURE_UNIT})", "마지막 수신", "비고"]
    st.dataframe(table, hide_index=True)


# ═══════════════ 3단계: 압력 그래프 (실시간·기록 조회 공용) ═══════════════
LINE_COLORS = ["#1F77B4", "#FF7F0E", "#2CA02C", "#9467BD", "#8C564B", "#17BECF"]


def pressure_figure(raw, sensor_names: dict, x_range, height=380, note=None, bucket_sec=1):
    """
    압력 그래프 1장 만들기
      - 초록 배경 = 정상 범위
      - 빨간 점 = 압력 이상 / 수충격 의심 값 (항상 원본 1초 데이터 기준)
      - 데이터가 빠진 구간은 선을 끊어서 빈칸으로 표시
    raw: 측정값 표(여러 센서 가능), x_range: (시작, 끝)
    bucket_sec: 1보다 크면 선을 그 초 단위 평균으로 줄여서 그림 (긴 기간 조회용)
    """
    fig = go.Figure()
    # 정상 범위 배경
    fig.add_hrect(y0=config.NORMAL_MIN, y1=config.NORMAL_MAX, fillcolor="#2E9E5B",
                  opacity=0.10, line_width=0, layer="below")

    marked = rules.mark_anomalies(raw) if not raw.empty else raw
    for i, (sid, part) in enumerate(marked.groupby("sensor_id") if not raw.empty else []):
        if bucket_sec > 1:
            line = rules.downsample(part, bucket_sec)   # 평균으로 줄이기 (빈 칸은 NaN → 선이 끊김)
        else:
            line = rules.insert_gaps(part, config.GAP_SEC)  # 빈 구간에 NaN 넣기 → 선이 끊김
        fig.add_trace(go.Scatter(
            x=line["measured_at"], y=line["pressure"], mode="lines",
            name=sensor_names.get(sid, sid), connectgaps=False,
            line=dict(width=1.6, color=LINE_COLORS[i % len(LINE_COLORS)]),
            hovertemplate="%{x|%H:%M:%S}<br>%{y:.2f} " + config.PRESSURE_UNIT + "<extra>" + sensor_names.get(sid, sid) + "</extra>",
        ))
        bad = part[part["out_of_range"] | part["hammer"]]
        if not bad.empty:
            kinds = [", ".join(k for k, f in ((rules.EV_PRESSURE, o), (rules.EV_HAMMER, h)) if f)
                     for o, h in zip(bad["out_of_range"], bad["hammer"])]
            fig.add_trace(go.Scatter(
                x=bad["measured_at"], y=bad["pressure"], mode="markers",
                name=f"{sensor_names.get(sid, sid)} 이상", showlegend=False,
                marker=dict(color=STATUS_COLOR[rules.ST_WARNING], size=7, symbol="circle"),
                text=kinds, hovertemplate="%{x|%H:%M:%S}<br>%{y:.2f} " + config.PRESSURE_UNIT + "<br>%{text}<extra></extra>",
            ))

    fig.update_layout(
        height=height, margin=dict(l=10, r=10, t=30 if note else 10, b=10),
        xaxis_title="측정 시각", yaxis_title=f"압력 ({config.PRESSURE_UNIT})",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        hovermode="closest", title=dict(text=note, font=dict(size=13)) if note else None,
    )
    fig.update_xaxes(range=list(x_range))
    fig.update_yaxes(range=[0, 8])
    return fig


def draw_live_chart(conn, status_df, now):
    names = dict(zip(status_df["sensor_id"], status_df["name"]))
    # 센서 선택 (새로고침되어도 선택이 유지되도록 key 지정)
    sid = st.selectbox("센서 선택", list(names), format_func=lambda s: f"{s} · {names[s]}", key="live_sensor")
    start = now - timedelta(minutes=config.LIVE_WINDOW_MIN)
    raw = db.read_readings(conn, start, now, [sid])
    fig = pressure_figure(raw, names, (start, now))

    # 통신 두절 중이면 마지막 데이터 이후 구간을 회색으로 칠해 표시
    row = status_df.set_index("sensor_id").loc[sid]
    if row["status"] == rules.ST_OFFLINE:
        last = row["last_measured"] if pd.notna(row["last_measured"]) else start
        x0 = max(pd.Timestamp(last), pd.Timestamp(start))
        fig.add_vrect(x0=x0, x1=now, fillcolor=STATUS_COLOR[rules.ST_OFFLINE], opacity=0.25,
                      line_width=0, layer="below")
        fig.add_annotation(x=x0, y=7.6, text="통신 두절 중", showarrow=False, xanchor="left",
                           font=dict(color="#555"))
    st.plotly_chart(fig, key="live_chart")
    st.caption(f"최근 {config.LIVE_WINDOW_MIN}분 · 초록 배경 = 정상 범위({config.NORMAL_MIN}~{config.NORMAL_MAX}) · "
               f"빨간 점 = 이상 값 · 선이 끊긴 곳 = 데이터 없음")


# ═══════════════ 3단계: 알림 목록 ═══════════════
EVENT_ICON = {rules.EV_PRESSURE: "🔴", rules.EV_HAMMER: "🟠", rules.EV_COMM_LOST: "⚪", rules.EV_COMM_BACK: "🔵"}


def event_table(ev):
    """알림 표를 화면용 한국어 열 이름으로 바꿉니다."""
    out = ev.assign(
        구분=ev["event_type"].map(lambda t: f"{EVENT_ICON.get(t, '')} {t}"),
        발생시각=ev["event_time"].dt.strftime("%m-%d %H:%M:%S"),
        수신시각=ev["detected_at"].dt.strftime("%H:%M:%S"),
    )[["발생시각", "구분", "name", "message", "수신시각"]]
    out.columns = ["발생 시각", "구분", "센서", "내용", "서버 수신"]
    return out


def draw_alerts(conn):
    ev = db.read_events(conn, limit=config.ALERT_LIST_SIZE)
    if ev.empty:
        st.info("아직 알림이 없습니다.")
        return
    st.dataframe(event_table(ev), hide_index=True, height=380)
    st.caption("최신순 · '서버 수신'이 '발생 시각'보다 늦으면 통신 복구 후 한꺼번에 받은 데이터에서 찾은 이상입니다.")


# ═══════════════ 실시간 영역 (몇 초마다 자동 새로고침) ═══════════════
@st.fragment(run_every=config.REFRESH_SEC)
def live_section():
    now = config.now()
    conn = open_db()
    try:
        status_df = load_current_status(conn, now)
        if status_df["last_received"].isna().all() or rules.is_offline(status_df["last_received"].max(), now):
            st.warning("최근 데이터가 없습니다. 시뮬레이터(simulator.py)가 켜져 있는지 확인해 주세요.")

        st.caption(f"마지막 갱신 {now:%Y-%m-%d %H:%M:%S} · {config.REFRESH_SEC}초마다 자동 새로고침")
        draw_status_cards(status_df)

        left, right = st.columns([3, 2])
        with left:
            st.subheader("센서 위치")
            draw_map(status_df)
        with right:
            st.subheader("센서 상태")
            draw_sensor_table(status_df)

        left, right = st.columns([3, 2])
        with left:
            st.subheader("실시간 압력 그래프")
            draw_live_chart(conn, status_df, now)
        with right:
            st.subheader("알림 목록")
            draw_alerts(conn)
    finally:
        conn.close()


# ═══════════════ 4단계: 기록 조회 ═══════════════
def history_section():
    st.subheader("기록 조회")
    conn = open_db()
    try:
        sensors = db.read_sensors(conn)
        names = dict(zip(sensors["sensor_id"], sensors["name"]))

        # 입력 칸의 처음 값은 한 번만 정해 둡니다 (화면이 다시 그려져도 입력한 값이 유지되도록)
        if "h_start_date" not in st.session_state:
            now = config.now().replace(second=0)
            st.session_state.h_start_date = (now - timedelta(minutes=30)).date()
            st.session_state.h_start_time = (now - timedelta(minutes=30)).time()
            st.session_state.h_end_date = (now + timedelta(minutes=1)).date()
            st.session_state.h_end_time = (now + timedelta(minutes=1)).time()
            st.session_state.h_sensors = list(names)

        with st.form("history_form"):
            c1, c2, c3, c4 = st.columns(4)
            c1.date_input("시작 날짜", key="h_start_date")
            c2.time_input("시작 시각", key="h_start_time", step=60)
            c3.date_input("끝 날짜", key="h_end_date")
            c4.time_input("끝 시각", key="h_end_time", step=60)
            st.multiselect("센서", list(names), key="h_sensors", format_func=lambda s: f"{s} · {names[s]}")
            submitted = st.form_submit_button("조회", type="primary")

        if submitted:
            st.session_state.h_query = (
                datetime.combine(st.session_state.h_start_date, st.session_state.h_start_time),
                datetime.combine(st.session_state.h_end_date, st.session_state.h_end_time),
                list(st.session_state.h_sensors),
            )
        if "h_query" not in st.session_state:
            st.info("기간과 센서를 고른 뒤 [조회]를 눌러 주세요.")
            return

        start, end, sids = st.session_state.h_query
        if end <= start:
            st.error("끝 시각이 시작 시각보다 뒤여야 합니다.")
            return
        if end - start > timedelta(hours=config.HISTORY_MAX_HOURS):
            st.error(f"한 번에 최대 {config.HISTORY_MAX_HOURS}시간까지 조회할 수 있습니다.")
            return
        if not sids:
            st.error("센서를 1개 이상 골라 주세요.")
            return

        raw = db.read_readings(conn, start, end, sids)
        if raw.empty:
            st.info("이 기간에는 저장된 데이터가 없습니다.")
            return

        # 기간이 길면 점 개수를 약 1,500개로 줄여서 그림 (이상 값 빨간 점은 원본 기준 그대로)
        span = (end - start).total_seconds()
        bucket = max(1, int(span // 1500))
        note = f"{start:%m-%d %H:%M} ~ {end:%m-%d %H:%M}" + (f" · 선은 {bucket}초 평균" if bucket > 1 else "")
        st.plotly_chart(pressure_figure(raw, names, (start, end), height=420, note=note, bucket_sec=bucket),
                        key="history_chart")

        # 센서별 요약: 평균·최소·최대, 수신율(빠진 데이터가 얼마나 되는지)
        expected = int(span) + 1
        marked = rules.mark_anomalies(raw)
        summary = marked.groupby("sensor_id").agg(
            평균=("pressure", "mean"), 최소=("pressure", "min"), 최대=("pressure", "max"),
            건수=("pressure", "size"), 압력이상=("out_of_range", "sum"), 수충격의심=("hammer", "sum"),
        ).round(2).reset_index()
        summary["수신율(%)"] = (summary["건수"] / expected * 100).clip(upper=100).round(1)
        summary.insert(1, "센서", summary["sensor_id"].map(names))
        summary = summary.drop(columns="sensor_id")

        left, right = st.columns([3, 2])
        with left:
            st.markdown("**센서별 요약**")
            st.dataframe(summary, hide_index=True)
            st.download_button("이 기간 데이터 CSV 내려받기",
                               raw.to_csv(index=False).encode("utf-8-sig"),  # 엑셀에서 한글이 깨지지 않도록
                               file_name=f"pressure_{start:%Y%m%d_%H%M}_{end:%H%M}.csv", mime="text/csv")
        with right:
            st.markdown("**이 기간의 알림**")
            ev = db.read_events(conn, limit=500, start=start, end=end)
            ev = ev[ev["sensor_id"].isin(sids)]
            if ev.empty:
                st.caption("알림이 없습니다.")
            else:
                st.dataframe(event_table(ev), hide_index=True, height=260)
    finally:
        conn.close()


# ═══════════════ 왼쪽 메뉴: 시연 도구와 판단 기준 ═══════════════
def sidebar():
    with st.sidebar:
        st.header("시연 도구")
        st.caption("무작위 상황을 기다리지 않고 바로 보여주고 싶을 때 사용합니다. (시뮬레이터가 켜져 있어야 동작)")
        conn = open_db()
        try:
            sensors = db.read_sensors(conn)
            names = dict(zip(sensors["sensor_id"], sensors["name"]))
            sid = st.selectbox("대상 센서", list(names), format_func=lambda s: f"{s} · {names[s]}", key="demo_sensor")
            if st.button("💥 수충격 일으키기"):
                db.add_command(conn, sid, "hammer")
                st.success(f"{names[sid]}에 수충격 명령을 보냈습니다.")
            if st.button("📡 통신 끊기"):
                db.add_command(conn, sid, "outage")
                st.success(f"{names[sid]}의 통신을 끊었습니다. {config.COMM_TIMEOUT_SEC}초 뒤 '통신 두절'로 바뀝니다.")
        finally:
            conn.close()

        st.divider()
        st.header("판단 기준")
        st.markdown(
            f"- **압력 이상**: {config.NORMAL_MIN} ~ {config.NORMAL_MAX} {config.PRESSURE_UNIT} 벗어남\n"
            f"- **수충격 의심**: 1초 사이 {config.HAMMER_DELTA} 이상 변화\n"
            f"- **통신 두절**: {config.COMM_TIMEOUT_SEC}초 넘게 수신 없음\n"
            f"- 경고 표시 유지: {config.WARNING_HOLD_SEC}초\n\n"
            "기준값은 `config.py`에서 바꿀 수 있습니다."
        )


# ═══════════════ 화면 구성 ═══════════════
st.title("💧 상수도 관망 압력 관제 데모")
sidebar()
live_section()
st.divider()
history_section()

# 하단 안내 문구
st.divider()
st.markdown(
    "<div style='text-align:center; color:#888; font-size:0.9rem; padding:8px 0 16px;'>"
    "⚠️ 연습용 데모이며 실제 데이터가 아닙니다. 센서 위치와 압력 값은 모두 임의로 만든 것입니다."
    "</div>",
    unsafe_allow_html=True,
)
