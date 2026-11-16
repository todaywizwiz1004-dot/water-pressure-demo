"""
한 번에 실행하기 (run.bat 이 이 파일을 실행합니다)
------------------------------------------------
1) 처음 한 번: 이 폴더 안에 가상환경(.venv)을 만들고 필요한 패키지 설치
2) 시뮬레이터를 새 창에서 실행 (DB가 없으면 지난 30분 데이터를 먼저 채움)
3) 관제 화면(Streamlit)을 이 창에서 실행하고 브라우저를 엶
이 창을 Ctrl + C 로 끄면 시뮬레이터도 같이 꺼집니다.
"""
import os
import subprocess
import sys
import threading
import venv
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV = HERE / ".venv"
VENV_PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
INSTALLED_MARK = VENV / ".installed"
URL = "http://localhost:8501"


def main():
    if sys.version_info < (3, 11):
        print(f"[오류] 파이썬 3.11 이상이 필요합니다. 지금 버전: {sys.version.split()[0]}")
        print("https://www.python.org/downloads/ 에서 새 버전을 설치해 주세요.")
        sys.exit(1)

    # 1) 처음 실행이면 가상환경 만들고 패키지 설치
    if not INSTALLED_MARK.exists():
        print("처음 실행입니다. 필요한 프로그램을 설치합니다 (인터넷 필요, 몇 분 걸릴 수 있음)...")
        if not VENV_PY.exists():
            venv.create(VENV, with_pip=True)
        subprocess.check_call([str(VENV_PY), "-m", "pip", "install", "--disable-pip-version-check",
                               "-r", str(HERE / "requirements.txt")])
        INSTALLED_MARK.write_text("ok", encoding="utf-8")
        print("설치 완료!\n")

    # 2) 시뮬레이터를 새 창에서 실행
    sim_cmd = [str(VENV_PY), "simulator.py"]
    if not (HERE / "water_demo.db").exists():
        sim_cmd += ["--backfill", "30"]  # 기록 조회 시연용으로 지난 30분 데이터를 미리 채움
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    sim = subprocess.Popen(sim_cmd, cwd=HERE, creationflags=flags)
    print("시뮬레이터를 새 창에서 켰습니다.")

    # 3) 몇 초 뒤 브라우저 열기 + 관제 화면 실행
    threading.Timer(6, lambda: webbrowser.open(URL)).start()
    print(f"관제 화면을 켭니다: {URL}  (끄려면 이 창에서 Ctrl + C)\n")
    # 시뮬레이터가 별도 창에서 돌고 있으니, 화면 안에서는 시뮬레이터를 또 켜지 않도록 표시
    env = dict(os.environ, WATER_DEMO_EXTERNAL_SIM="1")
    try:
        subprocess.call([str(VENV_PY), "-m", "streamlit", "run", "app.py",
                         "--server.headless", "true"], cwd=HERE, env=env)
    except KeyboardInterrupt:
        pass
    finally:
        sim.terminate()
        print("시뮬레이터와 관제 화면을 종료했습니다.")


if __name__ == "__main__":
    main()
