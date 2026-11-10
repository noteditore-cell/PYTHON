"""
tiktok_uploader.py
Selenium Chrome automation: logs into TikTok (using a persistent Chrome
profile so you only log in manually once), opens the upload page, uploads
the video file, fills in the caption/hashtags, dismisses any popups/ads
along the way, waits for TikTok to actually finish processing the video
(the Post button stays disabled until then), and posts.

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
- NO input()/Enter PROMPTS ANYMORE. Earlier versions paused and waited for
  you to press Enter to confirm you'd posted manually — that was causing
  false "Done" reports when Enter got pressed without the post actually
  happening. Now: if posting is confirmed, the browser closes on its own.
  If it can't be confirmed, the script leaves the browser open and returns
  without claiming success — check the window yourself, no timer, no
  waiting on the terminal.
"""

import os
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException

import config

UPLOAD_URL = "https://www.tiktok.com/upload?lang=en"
HEADLESS = False  # keep False for first run / manual login


def get_driver():
    profile_dir = os.path.abspath(config.CHROME_PROFILE_DIR)
    os.makedirs(profile_dir, exist_ok=True)

    # Because Chrome now stays open on purpose after a run (see "detach"
    # below), a leftover window from the previous run will still be
    # holding this profile folder locked. Trying to start a second Chrome
    # on the same profile while that's true causes an ugly native crash.
    # Detect that case up front and fail with a clear instruction instead.
    lock_file = os.path.join(profile_dir, "SingletonLock")
    if os.path.exists(lock_file):
        try:
            os.remove(lock_file)  # only succeeds if nothing still holds it
        except OSError:
            raise RuntimeError(
                "A Chrome window from a previous run is still open and using "
                f"this same profile ({profile_dir}).\n"
                "Close ALL Chrome windows (Task Manager -> End Task on every "
                "'Google Chrome' entry) and run this again."
            )

    options = Options()
    options.add_argument(f"--user-data-dir={profile_dir}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    # Without this, Chrome closes itself the moment the Python script exits
    # (even if we never call driver.quit()) — which is what was closing the
    # browser before you could check whether the post actually went through.
    options.add_experimental_option("detach", True)
    if HEADLESS:
        options.add_argument("--headless=new")

    try:
        driver = webdriver.Chrome(options=options)
    except WebDriverException as e:
        raise RuntimeError(
            "Chrome failed to start. Most common causes:\n"
            "  1. Another Chrome window is already open using the same "
            f"profile folder ({profile_dir}) — close ALL Chrome windows "
            "and try again.\n"
            "  2. Google Chrome itself isn't installed, or is a very new "
            "version Selenium doesn't recognize yet — update Chrome.\n"
            f"Original error: {e}"
        )
    return driver


def wait_for_manual_login(driver, timeout=180):
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


POPUP_CLOSE_XPATHS = [
    "//button[@aria-label='Close']",
    "//div[@aria-label='Close']",
    "//button[contains(@class,'close')]",
    "//div[@role='dialog']//button[contains(text(),'Not now')]",
    "//div[@role='dialog']//button[contains(text(),'Skip')]",
    "//div[@role='dialog']//button[contains(text(),'Cancel')]",
    "//div[@role='dialog']//button[contains(text(),'Got it')]",
    "//div[@role='dialog']//button[contains(text(),'OK')]",
    "//button[contains(text(),'Not now')]",
    "//button[contains(text(),'Skip')]",
    "//button[contains(text(),'Accept')]",
    "//button[contains(text(),'Allow all')]",
    "//div[contains(@class,'modal')]//*[contains(@class,'close')]",
    "//*[@data-e2e='modal-close-inner-btn']",
    "//*[@data-e2e='cookie-banner-close']",
]


def dismiss_popups(driver, passes=3, pause=0.6):
    for _ in range(passes):
        closed_any = False
        for xpath in POPUP_CLOSE_XPATHS:
            try:
                elements = driver.find_elements(By.XPATH, xpath)
                for el in elements:
                    if el.is_displayed():
                        el.click()
                        closed_any = True
                        time.sleep(0.3)
            except Exception:
                continue
        try:
            webdriver.ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        except Exception:
            pass
        if not closed_any:
            break
        time.sleep(pause)


def _try_click_schedule_toggle(driver):
    candidates = [
        "//div[@data-e2e='schedule-switch']",
        "//div[text()='Schedule']/ancestor::div[3]//div[@role='switch']",
        "//span[text()='Schedule']/ancestor::div[2]//input",
        "//*[contains(text(),'Schedule')]",
    ]
    for xpath in candidates:
        try:
            el = driver.find_element(By.XPATH, xpath)
            el.click()
            return True
        except Exception:
            continue
    return False


POST_BUTTON_XPATHS = [
    "//button[contains(., 'Post')]",
    "//div[@data-e2e='post-button']//button",
    "//button[@data-e2e='post-button']",
]


def _find_post_button(driver):
    for xpath in POST_BUTTON_XPATHS:
        try:
            els = driver.find_elements(By.XPATH, xpath)
            for el in els:
                if el.is_displayed():
                    return el
        except Exception:
            continue
    return None


def _wait_until_post_enabled(driver, timeout=120):
    """TikTok keeps the Post button disabled while your video is still
    processing server-side (can take a while for longer videos / slow
    upload speeds). Clicking it while disabled does nothing, which is what
    was causing 'clicked Post' to silently not post anything. This polls
    until it's actually clickable, or gives up after `timeout` seconds."""
    end = time.time() + timeout
    while time.time() < end:
        btn = _find_post_button(driver)
        if btn is not None:
            disabled = btn.get_attribute("disabled")
            aria_disabled = btn.get_attribute("aria-disabled")
            if not disabled and aria_disabled != "true":
                return btn
        time.sleep(1.5)
    return None


def upload_video(video_path, caption, schedule_dt_local_str=None, timeout=120):
    """
    Returns True only if posting was genuinely confirmed. Returns False if
    it wasn't confirmed — in that case the browser is left OPEN (not
    closed) so you can check/finish it yourself with no time pressure.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video not found: {video_path}")

    driver = get_driver()
    confirmed = False
    should_close_browser = False

    try:
        wait_for_manual_login(driver)
        dismiss_popups(driver)

        driver.get(UPLOAD_URL)
        wait = WebDriverWait(driver, timeout)
        dismiss_popups(driver)

        file_input = wait.until(
            EC.presence_of_element_located((By.XPATH, "//input[@type='file']"))
        )
        file_input.send_keys(os.path.abspath(video_path))

        time.sleep(6)
        dismiss_popups(driver)
        time.sleep(4)

        caption_box = wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//div[@contenteditable='true' and @role='combobox']")
            )
        )
        dismiss_popups(driver)
        caption_box.click()
        caption_box.send_keys(Keys.CONTROL, "a")
        caption_box.send_keys(Keys.DELETE)
        for char in caption:
            caption_box.send_keys(char)
            time.sleep(0.01)

        time.sleep(2)
        dismiss_popups(driver)
        print("[uploader] video uploaded and caption typed successfully.")

        if schedule_dt_local_str:
            print("\n" + "=" * 60)
            print(f"SCHEDULE THIS FOR: {schedule_dt_local_str}")
            print("=" * 60)
            dismiss_popups(driver)
            toggled = _try_click_schedule_toggle(driver)
            if toggled:
                print("[uploader] clicked the Schedule toggle for you.")
            else:
                print("[uploader] couldn't find the Schedule toggle automatically "
                      "— click it yourself (it's near the Post button).")
            print(f"Set the date/time to: {schedule_dt_local_str}, then click "
                  "TikTok's own 'Schedule' button.")
            print("[uploader] leaving the browser open for you to finish this — "
                  "the script will NOT close it or wait on the terminal.")
            confirmed = False  # scheduling needs your manual click; don't claim success
            should_close_browser = False
        else:
            print("[uploader] waiting for TikTok to finish processing the video "
                  "before Post becomes clickable (can take a minute)...")
            dismiss_popups(driver)
            btn = _wait_until_post_enabled(driver, timeout=120)

            if btn is None:
                print("[uploader] Post button never became enabled/clickable within "
                      "2 minutes. The video may still be processing, or the page "
                      "layout changed. Browser is left OPEN — check it and click "
                      "Post yourself when it's ready.")
                confirmed = False
                should_close_browser = False
            else:
                # Use a JS click to avoid "element click intercepted" issues
                # from any invisible overlay sitting on top of the button.
                driver.execute_script("arguments[0].click();", btn)
                print("[uploader] clicked Post. Verifying...")
                try:
                    WebDriverWait(driver, 60).until(
                        lambda d: (
                            "/upload" not in d.current_url
                            or d.find_elements(By.XPATH,
                                "//*[contains(text(),'uploaded') or "
                                "contains(text(),'Manage') or "
                                "contains(text(),'View post') or "
                                "contains(text(),'has been posted')]")
                        )
                    )
                    print(f"[uploader] post confirmed — current page: {driver.current_url}")
                    confirmed = True
                    should_close_browser = True
                except Exception:
                    print("[uploader] clicked Post but couldn't positively confirm it "
                          "within 60s. IMPORTANT: this doesn't necessarily mean it "
                          "failed — newer TikTok upload pages sometimes show success "
                          "without changing the URL. Browser is left OPEN, please "
                          "look at it yourself to check.")
                    confirmed = False
                    should_close_browser = False

        return confirmed

    except Exception as e:
        print(f"[uploader] ran into a problem: {e}")
        print("Browser is left OPEN so nothing is lost — check it and finish "
              "manually if the video/caption made it that far.")
        return False

    finally:
        if should_close_browser:
            time.sleep(2)
            driver.quit()
        # otherwise: leave the browser open, don't touch it further


if __name__ == "__main__":
    test_path = input("Path to a test video file: ").strip()
    result = upload_video(test_path, "Test upload #fyp")
    print("Confirmed posted:", result)
