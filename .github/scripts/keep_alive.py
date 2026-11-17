"""Streamlit Community Cloud 앱이 잠들지 않도록 실제 브라우저로 접속한다.

단순 HTTP 요청(curl)은 앱 접속으로 인정되지 않으므로 헤드리스 Chromium 으로
페이지를 열고 웹소켓 연결이 맺어질 때까지 잠시 머문다.
앱이 이미 잠들어 있으면 "get this app back up" 버튼을 눌러 깨운다.
"""
import os
import re
import sys

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

URL = os.environ.get("APP_URL", "https://water-pressure-demo.streamlit.app/")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(8_000)

        wake = page.get_by_role("button", name=re.compile("get this app back up", re.I))
        if wake.count() > 0:
            print("앱이 잠들어 있음 -> 깨우기 버튼 클릭")
            wake.first.click()
            page.wait_for_timeout(60_000)
        else:
            print("앱이 깨어 있음")

        # 앱 본문(iframe 안)이 렌더링될 때까지 대기해 웹소켓 접속을 확실히 한다.
        try:
            page.frame_locator("iframe").first.locator("body").wait_for(timeout=30_000)
        except PWTimeout:
            pass
        page.wait_for_timeout(15_000)

        still_asleep = page.get_by_text(re.compile("get this app back up", re.I)).count()
        browser.close()

    if still_asleep:
        print("실패: 앱이 아직 잠들어 있음")
        return 1
    print("완료: 접속 성공")
    return 0


if __name__ == "__main__":
    sys.exit(main())
