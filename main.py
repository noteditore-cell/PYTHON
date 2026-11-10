"""
main.py
Main loop: pick topic -> make video -> upload to TikTok -> log to history.

Run with: python main.py
Or double-click run.bat on Windows.

By default this posts immediately and fully automatically (uploads video,
types caption, dismisses any popups/ads that appear, clicks Post, and
verifies it went through) — no manual steps needed if TikTok's page layout
matches what the script expects.

If you'd rather schedule for your region's peak-audience time instead of
posting right away, run:
    python main.py --schedule
Peak hours (same ones used in the YouTube pipeline):
    US   -> 23:00 UTC
    UK   -> 19:00 UTC
    BOTH -> 20:00 UTC
Scheduling needs one manual click (TikTok's date-picker widget isn't safe
to automate blindly), immediate posting does not.
"""

import sys
import datetime

import config
import video_maker
import tiktok_uploader

PEAK_HOUR_UTC = {"US": 23, "UK": 19, "BOTH": 20}

# Pakistan Standard Time is fixed at UTC+5 (no daylight saving), which is
# almost certainly your PC's local timezone and therefore the timezone
# TikTok's date/time picker will show in the browser.
PKT_OFFSET_HOURS = 5


def compute_next_schedule_time(region):
    """Returns (utc_datetime, local_pkt_string) for the next upcoming peak
    posting slot for the given region."""
    peak_hour = PEAK_HOUR_UTC.get(region, PEAK_HOUR_UTC["BOTH"])
    now_utc = datetime.datetime.now(datetime.timezone.utc)
    target_utc = now_utc.replace(hour=peak_hour, minute=0, second=0, microsecond=0)

    if target_utc <= now_utc + datetime.timedelta(minutes=20):
        target_utc += datetime.timedelta(days=1)

    target_pkt = target_utc + datetime.timedelta(hours=PKT_OFFSET_HOURS)
    pkt_str = target_pkt.strftime("%Y-%m-%d %I:%M %p") + " (Pakistan time, PKT)"
    return target_utc, pkt_str


def run_once(topic=None, use_schedule=False):
    print("=" * 60)
    print("STEP 1/2: Generating video...")
    result = video_maker.create_video(topic=topic)

    print(f"\nVideo ready: {result['video_path']}")
    print(f"Topic: {result['topic']}")
    print(f"Caption: {result['metadata']['caption']}\n")

    schedule_str = None
    if use_schedule:
        _, schedule_str = compute_next_schedule_time(config.TARGET_REGION)
        print(f"STEP 2/2: Scheduling upload for {config.TARGET_REGION} peak time "
              f"-> {schedule_str}")
    else:
        print("STEP 2/2: Uploading to TikTok now — fully automatic "
              "(popups/ads will be auto-dismissed)...")

    try:
        posted = tiktok_uploader.upload_video(
            video_path=result["video_path"],
            caption=result["metadata"]["caption"],
            schedule_dt_local_str=schedule_str,
        )
        if posted:
            print("Done — post confirmed, browser closed itself.")
        else:
            print("NOT confirmed posted. The browser was left OPEN on purpose — "
                  "go check it and finish/verify manually. It will not close on "
                  "its own until a post is confirmed.")
    except Exception as e:
        print(f"[main] upload step failed: {e}")
        print(f"The finished video is still saved at: {result['video_path']}")
        print("You can upload it manually to TikTok if the automation didn't work.")

    return result


def main():
    args = sys.argv[1:]
    use_schedule = "--schedule" in args
    topic_args = [a for a in args if a != "--schedule"]
    topic = " ".join(topic_args) if topic_args else None

    if topic:
        print(f"Using topic from command line: {topic}")

    try:
        run_once(topic=topic, use_schedule=use_schedule)
    except Exception as e:
        print(f"[main] pipeline failed: {e}")
        raise


if __name__ == "__main__":
    main()
