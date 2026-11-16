"""
가짜 센서 데이터 생성기 (시뮬레이터)
====================================
실제 장비 대신 압력 센서 6개를 흉내 냅니다. 이 프로그램 하나가 두 가지 역할을 합니다.

  1) 현장 센서(FieldSensor)  : 1초마다 압력을 재고 서버로 보냄
       - 가끔 '수충격' : 몇 초 동안 압력이 급격히 오르내림
       - 가끔 '통신 두절': 30초~1분 동안 데이터를 못 보냄
         → 그동안 잰 값은 장비 안에 보관했다가, 다시 연결되면 한꺼번에 보냄 (저장 후 전송)
         → 재연결 때 이미 보낸 데이터 일부를 또 보내 '중복 전송'도 흉내 냄
  2) 수집 서버(CollectionServer): 받은 데이터를 DB에 저장하고 이상 여부를 판단해 알림을 남김
       - (센서 ID + 측정 시각)이 같은 데이터는 한 번만 저장

실행 방법
  python simulator.py                 # 실시간으로 계속 실행 (멈출 때는 Ctrl + C)
  python simulator.py --backfill 30   # 지난 30분 데이터를 먼저 채운 뒤 실시간 실행
  python simulator.py --reset         # 기존 DB를 지우고 새로 시작
"""
import argparse
import random
import time
from collections import deque
from datetime import datetime, timedelta

import config
import db
import rules


# ═══════════════════════ 1) 현장 센서 흉내 ═══════════════════════
class FieldSensor:
    def __init__(self, info: dict):
        self.sensor_id = info["sensor_id"]
        self.name = info["name"]
        self.base = random.uniform(3.8, 4.2)       # 센서마다 평소 압력이 조금씩 다름
        self.pressure = self.base
        self.hammer_left = 0                       # 남은 수충격 시간(초)
        self.hammer_swing = 0.0
        self.offline_until = None                  # 통신 두절이 끝나는 시각 (None 이면 정상 연결)
        self.buffer = deque(maxlen=config.DEVICE_BUFFER_SIZE)  # 통신 두절 중 보관하는 데이터
        self.lost = 0                              # 보관 한도를 넘어 사라진 데이터 수
        self.recent_sent = deque(maxlen=config.RESEND_ON_RECONNECT)  # 최근에 보낸 데이터 (중복 전송용)

    def measure(self) -> float:
        """압력 1회 측정"""
        # 평상시: 3.5~4.5 사이에서 조금씩 흔들림 (기준값 쪽으로 살짝 끌려오는 무작위 걸음)
        self.pressure += random.gauss(0, config.NOISE) + (self.base - self.pressure) * 0.1
        self.pressure = min(max(self.pressure, config.BASE_MIN), config.BASE_MAX)

        # 수충격 시작 여부 (무작위)
        if self.hammer_left == 0 and random.random() < config.HAMMER_PROB:
            self.hammer_left = random.randint(*config.HAMMER_DURATION)
            self.hammer_swing = random.uniform(*config.HAMMER_SWING)

        # 수충격 중: 위·아래로 번갈아 크게 튀고 점점 잦아듦
        if self.hammer_left > 0:
            sign = 1 if self.hammer_left % 2 == 0 else -1
            value = self.pressure + sign * self.hammer_swing
            self.hammer_swing *= 0.85
            self.hammer_left -= 1
            return round(value, 3)
        return round(self.pressure, 3)

    def force_hammer(self):
        """시연 버튼: 지금 바로 수충격 시작"""
        self.hammer_left = random.randint(*config.HAMMER_DURATION)
        self.hammer_swing = random.uniform(*config.HAMMER_SWING)

    def is_connected(self, now: datetime) -> bool:
        return self.offline_until is None or now >= self.offline_until

    def start_outage(self, now: datetime):
        self.offline_until = now + timedelta(seconds=random.randint(*config.OUTAGE_DURATION))
        self.lost = 0

    def tick(self, now: datetime, can_go_offline: bool):
        """1초에 한 번 호출. 서버로 보낼 데이터 목록과 상황 메모를 돌려줍니다."""
        value = self.measure()
        reading = (now, value)

        # 통신 두절 중 → 보낼 수 없으니 장비 안에 보관
        if self.offline_until is not None and now < self.offline_until:
            if len(self.buffer) == self.buffer.maxlen:
                self.lost += 1  # 가장 오래된 데이터가 밀려나 사라짐
            self.buffer.append(reading)
            return [], None

        # 통신이 막 다시 연결됨 → 보관해 둔 데이터를 한꺼번에 보냄 (+ 이미 보낸 것 일부 재전송)
        if self.offline_until is not None:
            self.offline_until = None
            packets = list(self.recent_sent) + list(self.buffer) + [reading]
            note = {"backlog": len(self.buffer) + 1, "lost": self.lost}
            self.buffer.clear()
            self.recent_sent.extend(packets[-config.RESEND_ON_RECONNECT:])
            return packets, note

        # 평상시 → 새 통신 두절이 시작될 수도 있음 (무작위)
        if can_go_offline and random.random() < config.OUTAGE_PROB:
            self.start_outage(now)
            self.buffer.append(reading)
            return [], None

        packets = [reading]
        if self.recent_sent and random.random() < config.RANDOM_DUPLICATE_PROB:
            packets.append(self.recent_sent[-1])  # 가끔 같은 데이터를 또 보냄
        self.recent_sent.append(reading)
        return packets, None


# ═══════════════════════ 2) 수집 서버 흉내 ═══════════════════════
class CollectionServer:
    def __init__(self, conn, quiet: bool = False):
        self.conn = conn
        self.quiet = quiet
        self.names = {s["sensor_id"]: s["name"] for s in config.SENSORS}
        self.last_alert = {}  # (센서 ID, 알림 종류) → 마지막 알림 측정 시각 (알림 폭주 방지)
        self.stats = {"saved": 0, "duplicate": 0}

    def log(self, text: str):
        if not self.quiet:
            print(text, flush=True)

    def receive(self, sensor_id: str, packets: list, now: datetime):
        """센서가 보낸 데이터 묶음을 저장. (새로 저장된 수, 중복이라 무시한 수)를 돌려줌"""
        saved = dup = 0
        for measured_at, pressure in sorted(packets):  # 측정 시각 순서대로 처리
            if db.insert_reading(self.conn, sensor_id, measured_at, pressure, now):
                saved += 1
                self.judge(sensor_id, measured_at, pressure, now)
            else:
                dup += 1  # 이미 있는 데이터 → 저장하지 않음
        self.stats["saved"] += saved
        self.stats["duplicate"] += dup
        return saved, dup

    def judge(self, sensor_id, measured_at, pressure, now):
        """새로 저장된 값 1건에 대해 이상 여부 판단"""
        name = self.names[sensor_id]
        if rules.is_out_of_range(pressure):
            self.alert(sensor_id, rules.EV_PRESSURE, measured_at, now,
                       f"압력 {pressure:.2f} {config.PRESSURE_UNIT} "
                       f"(정상 {config.NORMAL_MIN}~{config.NORMAL_MAX})")
        prev_time, prev_p = db.previous_reading(self.conn, sensor_id, measured_at)
        if rules.is_water_hammer(prev_p, prev_time, pressure, measured_at):
            self.alert(sensor_id, rules.EV_HAMMER, measured_at, now,
                       f"1초 사이 {prev_p:.2f} → {pressure:.2f} "
                       f"({abs(pressure - prev_p):.2f} 변화, 기준 {config.HAMMER_DELTA})")

    def alert(self, sensor_id, ev_type, event_time, now, message, force=False):
        key = (sensor_id, ev_type)
        last = self.last_alert.get(key)
        if not force and last and abs((event_time - last).total_seconds()) < config.ALERT_COOLDOWN_SEC:
            return  # 방금 같은 알림을 남겼으면 건너뜀
        self.last_alert[key] = event_time
        db.add_event(self.conn, event_time, now, sensor_id, ev_type, message)
        self.log(f"  [알림] {db.to_text(event_time)}  {self.names[sensor_id]}  {ev_type} - {message}")

    def check_comm(self, now: datetime, notes: dict):
        """센서별 마지막 수신 시각을 보고 통신 두절 / 복구를 판단"""
        last = db.last_received(self.conn)
        state = db.get_comm_state(self.conn)
        for sid in self.names:
            offline_now = rules.is_offline(last.get(sid), now)
            was_offline = bool(state.get(sid, 0))
            if offline_now and not was_offline:
                t = last.get(sid)
                msg = f"마지막 수신 {t:%H:%M:%S} 이후 {config.COMM_TIMEOUT_SEC}초 넘게 데이터 없음" if t else "수신 기록 없음"
                self.alert(sid, rules.EV_COMM_LOST, now, now, msg, force=True)
            elif was_offline and not offline_now:
                n = notes.get(sid, {})
                msg = f"밀린 데이터 {n.get('saved', 0)}건 일괄 수신 (중복 {n.get('duplicate', 0)}건 무시"
                if n.get("lost"):
                    msg += f", 장비 보관 한도 초과로 {n['lost']}건 유실"
                msg += ")"
                self.alert(sid, rules.EV_COMM_BACK, now, now, msg, force=True)
            if offline_now != was_offline:
                db.set_comm_state(self.conn, sid, offline_now)
        self.conn.commit()


# ═══════════════════════ 실행 부분 ═══════════════════════
def run_one_second(sensors, server, now: datetime):
    """1초 동안 일어나는 일: 모든 센서 측정·전송 → 서버 저장·판단 → 통신 상태 점검"""
    # 화면의 시연 버튼으로 들어온 명령 처리
    by_id = {s.sensor_id: s for s in sensors}
    for sid, cmd in db.take_commands(server.conn):
        s = by_id.get(sid)
        if s is None:
            continue
        if cmd == "hammer":
            s.force_hammer()
            server.log(f"  [시연] {s.name}: 수충격 일으킴")
        elif cmd == "outage" and s.is_connected(now):
            s.start_outage(now)
            server.log(f"  [시연] {s.name}: 통신 끊음 ({(s.offline_until - now).seconds}초)")

    offline_count = sum(not s.is_connected(now) for s in sensors)
    notes = {}
    for s in sensors:
        packets, note = s.tick(now, can_go_offline=offline_count < config.MAX_OFFLINE_SENSORS)
        offline_count = sum(not x.is_connected(now) for x in sensors)
        if packets:
            saved, dup = server.receive(s.sensor_id, packets, now)
            if note:  # 재연결 직후 한꺼번에 보낸 경우
                notes[s.sensor_id] = {"saved": saved, "duplicate": dup, "lost": note["lost"]}
                server.log(f"  [저장 후 전송] {s.name}: {len(packets)}건 수신 → 새로 저장 {saved}건, 중복 무시 {dup}건")
    server.conn.commit()
    server.check_comm(now, notes)


def main():
    parser = argparse.ArgumentParser(description="상수도 관망 압력 센서 시뮬레이터")
    parser.add_argument("--reset", action="store_true", help="기존 DB를 지우고 새로 시작")
    parser.add_argument("--backfill", type=int, default=0, help="지난 N분 데이터를 먼저 빠르게 채움")
    parser.add_argument("--seconds", type=int, default=0, help="N초만 실행하고 종료 (0 = 계속)")
    parser.add_argument("--seed", type=int, default=None, help="무작위 값 고정 (테스트용)")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)
    if args.reset:
        for suffix in ("", "-wal", "-shm"):
            p = config.DB_PATH.with_name(config.DB_PATH.name + suffix)
            if p.exists():
                p.unlink()
        print("기존 DB를 지웠습니다.")

    if is_already_running():
        print("다른 시뮬레이터가 이미 데이터를 보내고 있습니다. (관제 화면 안에서 돌고 있을 수 있음)")
        print("두 개가 동시에 돌면 데이터가 섞이므로 이 창은 종료합니다.")
        return
    run(backfill_min=args.backfill, seconds=args.seconds)


def is_already_running() -> bool:
    """최근 몇 초 안에 들어온 데이터가 있으면 다른 시뮬레이터가 돌고 있는 것으로 봅니다."""
    conn = db.connect()
    try:
        db.init_db(conn)
        last = db.last_received(conn)
    finally:
        conn.close()
    return bool(last) and (config.now() - max(last.values())).total_seconds() <= 3


def has_any_data() -> bool:
    conn = db.connect()
    try:
        db.init_db(conn)
        return bool(db.last_received(conn))
    finally:
        conn.close()


def run(backfill_min: int = 0, seconds: int = 0):
    """
    시뮬레이터 본체. 명령 창에서 실행할 때와
    관제 화면 안에서(인터넷 배포용) 함께 돌릴 때 모두 이 함수를 씁니다.
    """
    conn = db.connect()
    db.init_db(conn)
    sensors = [FieldSensor(info) for info in config.SENSORS]
    server = CollectionServer(conn)
    print(f"DB 파일: {config.DB_PATH}")

    # 지난 데이터 미리 채우기 (기록 조회 화면 시연용)
    if backfill_min > 0:
        print(f"지난 {backfill_min}분 데이터를 채우는 중...")
        server.quiet = True
        t = config.now() - timedelta(minutes=backfill_min)
        while t < config.now():
            run_one_second(sensors, server, t)
            t += timedelta(seconds=1)
        server.quiet = False
        print(f"완료: 저장 {server.stats['saved']}건, 중복 무시 {server.stats['duplicate']}건")

    print("시뮬레이터 시작 (멈추려면 Ctrl + C)")
    started = time.time()
    try:
        while True:
            # 매 초 정각에 맞춰 동작
            time.sleep(1 - (time.time() % 1))
            now = config.now()
            run_one_second(sensors, server, now)

            if now.second % 10 == 0:
                offline = [s.name for s in sensors if not s.is_connected(now)]
                print(f"{now:%H:%M:%S}  누적 저장 {server.stats['saved']}건 / 중복 무시 {server.stats['duplicate']}건"
                      + (f" / 통신 끊긴 센서: {', '.join(offline)}" if offline else ""), flush=True)
            if seconds and time.time() - started >= seconds:
                break
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
        print("시뮬레이터를 종료했습니다.")


if __name__ == "__main__":
    main()
