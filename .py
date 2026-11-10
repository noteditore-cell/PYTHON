"""
tiktok_uploader.py
Selenium Chrome automation: logs into TikTok (using a persistent Chrome
profile so you only log in manually once), opens the upload page, uploads
the video file, fills in the caption/hashtags, and posts.

IMPORTANT — read this before running:
- TikTok does not offer a public API for posting from unofficial apps, so
  this drives the real tiktok.com/upload web page like a human would.
- TikTok can and does detect and rate-limit/ban accounts that post in an
  automated, repetitive pattern. Space uploads out, use an account you
  don't mind losing, and don't run this on a tight loop.
- The very first run should be done with your Chrome window visible
  (HEADLESS = False here) so you can manually log in and pass any
  CAPTCHA/verification. After that, the persistent Chrome profile
  remembers your session for future runs.
"""

import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

import config

UPLOAD_URL = "https://www.tiktok.com/upload?lang=en"
HEADLESS = False  # keep False for first run / manual login


def get_driver():
    profile_dir = os.path.abspath(config.CHROME_PROFILE_DIR)
    os.makedirs(profile_dir, exist_ok=True)

    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    if HEADLESS:
        options.add_argument("--headless=new")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def wait_for_manual_login(driver, timeout=180):
    """If TikTok shows a login page, pause here so you can log in by hand
    the first time. After that, the saved Chrome profile keeps you logged in."""
    driver.get("https://www.tiktok.com/login")
    print("If a login form appears, log in manually now (you have up to "
          f"{timeout}s). If you're already logged in via the saved profile, "
          "this will just continue.")
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: "login" not in d.current_url
        )
    except Exception:
        print("[uploader] still on login page after timeout — continuing anyway, "
              "upload may fail if not logged in.")


def upload_video(video_path, caption, timeout=120):
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    driver = get_driver()
    try:
        wait_for_manual_login(driver)

        driver.get(UPLOAD_URL)
        wait = WebDriverWait(driver, timeout)

        # TikTok's upload page keeps the raw <input type="file"> in the DOM
        # even though the visible UI is a drag-and-drop box.
        file_input = wait.until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
        )
        file_input.send_keys(os.path.abspath(video_path))

        # Wait for the video to finish processing/preview to appear
        time.sleep(8)

        # Caption field: TikTok uses a contenteditable div for the caption box
        caption_box = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[@contenteditable='true' and @role='combobox']")
            )
        )
        caption_box.click()
        # select-all + delete any default text, then type the caption
        caption_box.send_keys(Keys.CONTROL, "a")
        caption_box.send_keys(Keys.DELETE)
        for char in caption:
            caption_box.send_keys(char)
            time.sleep(0.01)

        time.sleep(3)

        # Post button — TikTok's upload page markup changes over time;
        # try a few common patterns.
        post_button = None
        candidates = [
            "//button[contains(., 'Post') and not(@disabled)]",
            "//div[@data-e2e='post-button']//button",
            "//button[@data-e2e='post-button']",
        ]
        for xpath in candidates:
            try:
                post_button = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
                if post_button:
                    break
            except Exception:
                continue

        if not post_button:
            print("[uploader] could not find Post button automatically. "
                  "The browser is open — please click Post manually.")
            input("Press Enter here once you've posted (or to close the browser)...")
            return True

        post_button.click()
        time.sleep(5)
        print("[uploader] post submitted.")
        return True

    finally:
        # Leave the browser open a moment so you can see the result;
        # comment out driver.quit() if you want to inspect manually.
        time.sleep(5)
        driver.quit()


if __name__ == "__main__":
    # quick manual test
    test_path = input("Path to a test video file: ").strip()
    upload_video(test_path, "Test upload #fyp")
