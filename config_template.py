# config.py
# NOTE: You said config.py already exists in tiktok_bot/. If it does, just make sure
# it has these fields (add any that are missing). If it's empty/placeholder, replace
# it with this.

CHANNEL_NICHE      = ""                 # blank = auto-pick trending topic
TARGET_REGION      = "US"               # "US", "UK", or "BOTH"
PEXELS_API_KEY     = "kItn9K9tkyCuJ692vWvkPE9VU3L1gzzal6YN7gw2pIiobwuSvVm4RENp"
ANTHROPIC_API_KEY  = ""                 # blank = free template writer

# TikTok login (used by Selenium — NOT an API, just your normal login)
TIKTOK_EMAIL       = ""
TIKTOK_PASSWORD    = ""

CONTAINS_SYNTHETIC_MEDIA = False

WORKDIR   = "tiktok_pipeline"            # relative to tiktok_bot/ folder
CHROME_PROFILE_DIR = "chrome_profile"    # persists TikTok login/cookies between runs

# Same tested voices as the YouTube pipeline
VOICES_US = ["en-US-AndrewNeural", "en-US-BrianNeural"]
VOICES_UK = ["en-GB-ThomasNeural", "en-GB-RyanNeural"]

# TikTok videos are always vertical shorts
VIDEO_TYPE = "shorts"
TARGET_W, TARGET_H = 1080, 1920
