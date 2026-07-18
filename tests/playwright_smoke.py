from pathlib import Path

from playwright.sync_api import sync_playwright


URL = "http://127.0.0.1:8000/"
SCREENSHOT = Path("/tmp/grid_trading_streamlit_smoke.png")


def main() -> None:
    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on(
            "console",
            lambda message: console_errors.append(message.text)
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        response = page.goto(URL, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_load_state("networkidle", timeout=30_000)
        page.screenshot(path=str(SCREENSHOT), full_page=True)

        print(f"status={response.status if response else 'no_response'}")
        print(f"title={page.title()!r}")
        print(f"url={page.url}")
        print(f"headings={page.get_by_role('heading').all_inner_texts()}")
        print(f"buttons={page.get_by_role('button').all_inner_texts()}")
        print(f"inputs={page.locator('input').count()}")
        print(f"console_errors={console_errors}")
        print(f"page_errors={page_errors}")
        print(f"screenshot={SCREENSHOT}")
        browser.close()


if __name__ == "__main__":
    main()
