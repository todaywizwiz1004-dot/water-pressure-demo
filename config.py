"""
설정값 모음
-----------
이상 판단 기준, 시뮬레이션 확률, 화면 새로고침 주기 등을
이 파일 한 곳에서 바꿀 수 있습니다. 숫자만 고치고 프로그램을 다시 켜면 반영됩니다.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── 시각 기준: 한국 시간(UTC+9) ──
# 인터넷 서버는 보통 영국 기준 시각(UTC)으로 돌아가므로, 어디서 실행해도 한국 시각으로 맞춥니다.
KST = timezone(timedelta(hours=9))


def now() -> datetime:
    """지금 한국 시각 (초 단위까지)"""
    return datetime.now(KST).replace(tzinfo=None, microsecond=0)

# ── 데이터베이스 파일 위치 (이 폴더 안에 water_demo.db 파일 하나로 저장) ──
DB_PATH = Path(__file__).resolve().parent / "water_demo.db"

# ── 압력 센서 6개 (경기도 부천시 일대, 위치는 임의로 정한 값) ──
SENSORS = [
    {"sensor_id": "S01", "name": "중동 배수지 출구",   "lat": 37.5040, "lon": 126.7750},
    {"sensor_id": "S02", "name": "상동 호수공원",      "lat": 37.5020, "lon": 126.7480},
    {"sensor_id": "S03", "name": "송내역 인근",        "lat": 37.4877, "lon": 126.7530},
    {"sensor_id": "S04", "name": "역곡역 인근",        "lat": 37.4853, "lon": 126.8110},
    {"sensor_id": "S05", "name": "오정동 산업단지",    "lat": 37.5300, "lon": 126.7930},
    {"sensor_id": "S06", "name": "원미산 가압장",      "lat": 37.4960, "lon": 126.7890},
]

PRESSURE_UNIT = "kgf/cm²"

# ═══════════════ 이상 판단 규칙 ═══════════════
NORMAL_MIN = 2.5            # 정상 범위 아래쪽 한계 (이보다 낮으면 "압력 이상")
NORMAL_MAX = 5.5            # 정상 범위 위쪽 한계 (이보다 높으면 "압력 이상")
HAMMER_DELTA = 1.0          # 1초 사이 이만큼 이상 변하면 "수충격 의심"
HAMMER_MAX_GAP_SEC = 1      # 몇 초 간격의 두 값까지 비교할지 (1 = 바로 이웃한 1초 값만)
COMM_TIMEOUT_SEC = 20       # 마지막 데이터가 이 시간(초) 넘게 안 오면 "통신 두절"

# 화면에서 '경고' 상태를 얼마나 유지해서 보여줄지 (초)
# 수충격은 몇 초 만에 끝나므로, 너무 짧으면 화면에서 빨간 점을 놓칠 수 있습니다.
WARNING_HOLD_SEC = 15

# 같은 센서·같은 종류의 압력 알림은 이 시간(초) 안에 다시 쌓지 않음 (알림 폭주 방지)
ALERT_COOLDOWN_SEC = 10

# ═══════════════ 가짜 센서(시뮬레이터) 설정 ═══════════════
BASE_MIN = 3.5              # 평상시 압력 하한
BASE_MAX = 4.5              # 평상시 압력 상한
NOISE = 0.05                # 1초마다 흔들리는 정도

HAMMER_PROB = 0.003         # 센서 1개가 1초마다 수충격을 일으킬 확률 (6개 합쳐 약 1분에 1번)
HAMMER_DURATION = (4, 8)    # 수충격이 이어지는 시간 (초, 최소~최대)
HAMMER_SWING = (1.4, 2.2)   # 수충격 때 압력이 위아래로 튀는 폭

OUTAGE_PROB = 0.002         # 센서 1개가 1초마다 통신이 끊길 확률
OUTAGE_DURATION = (30, 60)  # 통신 두절이 이어지는 시간 (초)
MAX_OFFLINE_SENSORS = 2     # 동시에 끊길 수 있는 센서 최대 수

# 현장 장비가 통신 두절 중 보관할 수 있는 데이터 개수 (메모리 한계 흉내)
# 이보다 오래 끊기면 가장 오래된 데이터부터 사라져 그래프에 '빈 구간'이 생깁니다.
DEVICE_BUFFER_SIZE = 40

RESEND_ON_RECONNECT = 5     # 재연결 시 이미 보냈던 최근 데이터를 몇 개 다시 보낼지 (중복 전송 흉내)
RANDOM_DUPLICATE_PROB = 0.01  # 평상시에도 같은 데이터를 한 번 더 보낼 확률

# ═══════════════ 화면 설정 ═══════════════
REFRESH_SEC = 3             # 화면 자동 새로고침 주기 (초)
LIVE_WINDOW_MIN = 5         # 실시간 그래프에 보여줄 시간 (분)
GAP_SEC = 3                 # 이 시간(초) 넘게 데이터가 비면 그래프 선을 끊어서 표시
ALERT_LIST_SIZE = 50        # 알림 목록에 보여줄 최대 개수
HISTORY_MAX_HOURS = 24      # 기록 조회 한 번에 볼 수 있는 최대 기간 (시간)
