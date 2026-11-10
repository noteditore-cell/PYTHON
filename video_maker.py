"""
video_maker.py
Same generation pipeline as the YouTube Colab notebook, adapted for local
Windows use and TikTok's vertical (1080x1920) format.

Pipeline: topic -> wikipedia facts -> script -> voiceover (edge-tts/gtts)
          -> pexels clips -> ffmpeg assembly (zoom+color grade) -> whisper
          captions -> sound effects -> thumbnail (cover frame)
"""

import os
import re
import json
import random
import subprocess
import time
import uuid
import requests

import config

# ---------------------------------------------------------------------------
# Paths / history
# ---------------------------------------------------------------------------

def get_workdir(topic):
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", topic)[:40]
    folder = f"{safe}_{uuid.uuid4().hex[:6]}"
    path = os.path.join(config.WORKDIR, folder)
    os.makedirs(path, exist_ok=True)
    return path


HISTORY_FILE = os.path.join(config.WORKDIR, "history.json") if os.path.isabs(config.WORKDIR) \
    else os.path.join(os.path.dirname(os.path.abspath(__file__)), config.WORKDIR, "history.json")


def load_history():
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"topics_used": [], "videos": []}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


# ---------------------------------------------------------------------------
# Topic bank (fallback if you don't wire up trends)
# ---------------------------------------------------------------------------

TOPIC_BANK = [
    "Black holes", "Ancient Rome", "Human memory", "Deep sea creatures",
    "The Cold War", "Quantum physics", "Ancient Egypt", "The human brain",
    "World War II", "Space exploration", "Psychology of habits",
    "Vikings", "Ancient Greece", "The Roman Empire", "Dreams and sleep",
    "Artificial intelligence history", "The moon landing", "Sharks",
    "Volcanoes", "The Great Wall of China",
]


def pick_topic(history):
    used = set(history.get("topics_used", []))
    choices = [t for t in TOPIC_BANK if t not in used] or TOPIC_BANK
    topic = random.choice(choices)
    history.setdefault("topics_used", []).append(topic)
    save_history(history)
    return topic


# ---------------------------------------------------------------------------
# Script generation
# ---------------------------------------------------------------------------

HOOKS = [
    "You won't believe what scientists just discovered about {topic}...",
    "This one fact about {topic} will change how you see everything.",
    "Nobody talks about this part of {topic}, but they should.",
    "Here's the truth about {topic} that most people don't know.",
]

ENDINGS = [
    "Follow for more facts like this every day!",
    "Which fact surprised you most? Let me know in the comments.",
    "Save this video and share it with someone who'd love this.",
    "Follow for daily facts about the world around you!",
]


WIKI_HEADERS = {
    # Wikipedia's API rejects/blocks requests with no User-Agent (or the
    # default python-requests one), returning a non-JSON error page which is
    # what caused the "Expecting value" crash. A real UA fixes it.
    "User-Agent": "TikTokFactsBot/1.0 (personal automation project; contact: noteditore@gmail.com)"
}


def fetch_wikipedia_facts(topic, n=8):
    facts = []
    try:
        search_url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "query", "list": "search", "srsearch": topic,
            "format": "json", "srlimit": 1,
        }
        r = requests.get(search_url, params=params, headers=WIKI_HEADERS, timeout=10)
        if r.status_code != 200:
            print(f"[wikipedia] search request returned status {r.status_code}, "
                  f"body starts with: {r.text[:120]!r}")
            return facts
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return facts
        title = results[0]["title"]

        extract_url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + \
            requests.utils.quote(title)
        r2 = requests.get(extract_url, headers=WIKI_HEADERS, timeout=10)
        if r2.status_code == 200:
            extract = r2.json().get("extract", "")
            sentences = re.split(r"(?<=[.!?])\s+", extract)
            facts = [s.strip() for s in sentences if len(s.strip()) > 25][:n]
        else:
            print(f"[wikipedia] extract request returned status {r2.status_code}, "
                  f"body starts with: {r2.text[:120]!r}")
    except Exception as e:
        print(f"[wikipedia] fetch failed: {e}")
    return facts


def generate_script_template(topic, video_type="shorts"):
    facts = fetch_wikipedia_facts(topic, n=8)
    if not facts:
        facts = [
            f"{topic} has a surprising history that most people never learn about.",
            f"There are strange details about {topic} that scientists are still studying.",
            f"{topic} connects to parts of everyday life in ways you wouldn't expect.",
        ]

    max_facts = 4 if video_type == "shorts" else 8
    facts = facts[:max_facts]

    hook = random.choice(HOOKS).format(topic=topic)
    ending = random.choice(ENDINGS)

    script = hook + " " + " ".join(facts) + " " + ending
    return script.strip()


def generate_script_claude(topic, video_type, api_key):
    """Optional: use the Anthropic API for a punchier script instead of the
    free template writer. Only runs if ANTHROPIC_API_KEY is set in config."""
    if not api_key:
        return generate_script_template(topic, video_type)
    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 400,
                "messages": [{
                    "role": "user",
                    "content": (
                        f"Write a {'45-60 second' if video_type=='shorts' else '2 minute'} "
                        f"narration script about '{topic}' for a faceless TikTok facts video. "
                        "Punchy hook first line, short punchy sentences, end with a follow/share CTA. "
                        "Plain text only, no stage directions, no headers."
                    ),
                }],
            },
            timeout=30,
        )
        data = resp.json()
        text = "".join(b.get("text", "") for b in data.get("content", []))
        return text.strip() or generate_script_template(topic, video_type)
    except Exception as e:
        print(f"[claude] script generation failed, using template: {e}")
        return generate_script_template(topic, video_type)


# ---------------------------------------------------------------------------
# Voiceover
# ---------------------------------------------------------------------------

def get_best_voice():
    region = config.TARGET_REGION
    if region == "US":
        pool = config.VOICES_US
    elif region == "UK":
        pool = config.VOICES_UK
    else:
        pool = config.VOICES_US + config.VOICES_UK
    return random.choice(pool)


def make_voiceover(text, voice, out_path, attempts=5):
    import asyncio
    import edge_tts

    async def _run():
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(out_path)

    for attempt in range(attempts):
        try:
            asyncio.run(_run())
            if os.path.exists(out_path) and os.path.getsize(out_path) > 5000:
                return True
        except Exception as e:
            print(f"[edge-tts] attempt {attempt+1} failed: {e}")
        time.sleep(1)

    # gTTS fallback
    try:
        from gtts import gTTS
        gTTS(text).save(out_path)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 5000
    except Exception as e:
        print(f"[gtts] fallback failed: {e}")
        return False


def get_audio_duration(path):
    from mutagen.mp3 import MP3
    try:
        return MP3(path).info.length
    except Exception:
        return get_media_duration_ffprobe(path)


def get_media_duration_ffprobe(path):
    try:
        out = subprocess.check_output([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", path,
        ])
        return float(out.strip())
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Pexels clips
# ---------------------------------------------------------------------------

def fetch_pexels_clips(topic, outdir, needed_seconds, fallback_queries=None):
    queries = [topic] + (fallback_queries or ["nature", "abstract background", "city timelapse"])
    clip_paths = []
    headers = {"Authorization": config.PEXELS_API_KEY}

    for q in queries:
        if clip_paths:
            break
        try:
            r = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": q, "per_page": 10, "orientation": "portrait"},
                timeout=15,
            )
            videos = r.json().get("videos", [])
            for v in videos:
                files = sorted(v.get("video_files", []), key=lambda f: f.get("width", 0))
                hd = next((f for f in files if f.get("width", 0) >= 720), files[-1] if files else None)
                if not hd:
                    continue
                clip_path = os.path.join(outdir, f"clip_{len(clip_paths)}.mp4")
                with requests.get(hd["link"], stream=True, timeout=30) as resp:
                    with open(clip_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                clip_paths.append(clip_path)
                if len(clip_paths) >= 6:
                    break
        except Exception as e:
            print(f"[pexels] query '{q}' failed: {e}")

    return clip_paths


# ---------------------------------------------------------------------------
# Video assembly
# ---------------------------------------------------------------------------

ZOOM_VARIANTS = [
    "zoompan=z='min(zoom+0.0015,1.3)':d=125:s={w}x{h}",
    "zoompan=z='if(lte(zoom,1.0),1.3,max(1.0,zoom-0.0015))':d=125:s={w}x{h}",
    "zoompan=z='min(zoom+0.001,1.2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=125:s={w}x{h}",
]


def process_clip(clip_path, out_path, duration_target=6):
    w, h = config.TARGET_W, config.TARGET_H
    n_frames = int(duration_target * 25)
    zoom = random.choice(ZOOM_VARIANTS).format(w=w, h=h).replace("d=125", f"d={n_frames}")
    vf = (
        f"scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},{zoom},"
        "eq=contrast=1.08:saturation=1.15:brightness=0.02,"
        "fade=t=in:st=0:d=0.3,fade=t=out:st={dur_minus}:d=0.3"
    ).format(dur_minus=max(duration_target - 0.3, 0))

    cmd = [
        "ffmpeg", "-y", "-i", clip_path, "-t", str(duration_target),
        "-vf", vf, "-an", "-r", "25",
        # zoompan is CPU-heavy at 1080x1920; ultrafast preset trades a
        # little file size for much faster encoding, which matters a lot
        # here since we do this once per clip.
        "-preset", "ultrafast", "-threads", "0",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True)


def assemble_video(clip_paths, voiceover_path, outdir):
    audio_dur = get_audio_duration(voiceover_path)
    per_clip = 6
    n_needed = max(1, int(audio_dur // per_clip) + 1)

    processed = []
    i = 0
    while len(processed) < n_needed:
        src = clip_paths[i % len(clip_paths)]
        out_path = os.path.join(outdir, f"t{len(processed)}.mp4")
        print(f"[assemble] processing clip {len(processed) + 1}/{n_needed} "
              "(zoom + color grade — this is the slow part, hang tight)...")
        process_clip(src, out_path, per_clip)
        processed.append(out_path)
        i += 1

    print("[assemble] concatenating clips...")
    concat_list = os.path.join(outdir, "concat_list.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in processed:
            f.write(f"file '{os.path.abspath(p)}'\n")

    concat_path = os.path.join(outdir, "concat.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
        "-c", "copy", concat_path,
    ], capture_output=True)

    print("[assemble] mixing audio...")
    # background music bed (simple synth chord, quiet)
    bg_music = os.path.join(outdir, "bg_music.aac")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=220:duration={audio_dur}",
        "-af", "volume=0.01", bg_music,
    ], capture_output=True)

    mixed = os.path.join(outdir, "mixed.aac")
    subprocess.run([
        "ffmpeg", "-y", "-i", voiceover_path, "-i", bg_music,
        "-filter_complex",
        "[0:a]volume=5.0[a0];[1:a]volume=0.01[a1];[a0][a1]amix=inputs=2:duration=first:weights=50 1[aout]",
        "-map", "[aout]", mixed,
    ], capture_output=True)

    print("[assemble] muxing final video...")
    final_video = os.path.join(outdir, "final_video.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-i", concat_path, "-i", mixed,
        "-c:v", "libx264", "-c:a", "aac", "-shortest", final_video,
    ], capture_output=True)

    return final_video, audio_dur


# ---------------------------------------------------------------------------
# Captions (Whisper word-by-word)
# ---------------------------------------------------------------------------

CAPTION_COLORS = ["yellow", "white", "cyan", "orange", "green", "pink"]
CHUNK = 15


def add_captions(video_path, outdir):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[captions] faster-whisper not installed, skipping captions")
        return video_path

    print("[captions] transcribing audio (this can take a bit on CPU)...")
    model = WhisperModel("base")
    segments, _ = model.transcribe(video_path, word_timestamps=True)

    words = []
    for seg in segments:
        for w in (seg.words or []):
            words.append((w.start, w.end, w.word.strip()))

    if not words:
        print("[captions] no words detected, skipping captions")
        return video_path

    print(f"[captions] building caption filter for {len(words)} words...")

    # Build ONE drawtext filter per word and pass the whole chain to ffmpeg via
    # a filter script FILE (-filter_script:v) instead of a command-line string.
    # This does the encode in a single pass instead of re-encoding the video
    # once per chunk (the old approach), which is what was making this step
    # take many minutes — each chunk was a full CPU video re-encode.
    filter_parts = []
    for idx, (start, end, word) in enumerate(words):
        color = CAPTION_COLORS[idx % len(CAPTION_COLORS)]
        safe_word = (
            word.replace("'", "").replace(":", "").replace("\\", "").replace("%", "")
        )
        if not safe_word:
            continue
        filter_parts.append(
            f"drawtext=text='{safe_word}':fontcolor={color}:fontsize=48:"
            f"x=(w-text_w)/2:y=h*0.8:enable='between(t,{start:.2f},{end:.2f})'"
        )

    if not filter_parts:
        return video_path

    filter_file = os.path.join(outdir, "captions_filter.txt")
    with open(filter_file, "w", encoding="utf-8") as f:
        f.write(",".join(filter_parts))

    final_captioned = os.path.join(outdir, "final_captioned.mp4")
    print("[captions] encoding captions onto video (single pass)...")
    result = subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-filter_script:v", filter_file,
        "-preset", "veryfast",
        "-c:a", "copy", final_captioned,
    ], capture_output=True)

    if result.returncode == 0 and os.path.exists(final_captioned):
        print("[captions] done.")
        return final_captioned

    print(f"[captions] failed, using video without captions. "
          f"ffmpeg said: {result.stderr.decode(errors='ignore')[-300:]}")
    return video_path


# ---------------------------------------------------------------------------
# Sound effects
# ---------------------------------------------------------------------------

def add_sound_effects(video_path, audio_dur, outdir):
    sfx_types = {
        "whoosh": "aevalsrc='0.3*sin(2*PI*800*t)*exp(-3*t)':d=0.3",
        "impact": "aevalsrc='0.3*sin(2*PI*100*t)*exp(-5*t)':d=0.4",
        "ding": "aevalsrc='0.3*sin(2*PI*1200*t)*exp(-4*t)':d=0.5",
    }
    try:
        sfx_dir = os.path.join(outdir, "sfx")
        os.makedirs(sfx_dir, exist_ok=True)
        sfx_files = []
        for name, expr in sfx_types.items():
            path = os.path.join(sfx_dir, f"{name}.aac")
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", expr, path],
                            capture_output=True)
            sfx_files.append(path)

        n_hits = max(1, int(audio_dur // 4))
        inputs = ["-i", video_path]
        filter_parts = []
        for i in range(n_hits):
            sfx = random.choice(sfx_files)
            inputs += ["-i", sfx]
            t = round(random.uniform(1, max(audio_dur - 1, 1)), 2)
            filter_parts.append(f"[{i+1}:a]adelay={int(t*1000)}|{int(t*1000)},volume=0.3[s{i}]")

        mix_inputs = "".join(f"[s{i}]" for i in range(n_hits)) + f"[0:a]"
        filter_complex = ";".join(filter_parts) + \
            f";{mix_inputs}amix=inputs={n_hits+1}:duration=first[aout]"

        out_path = os.path.join(outdir, "with_sfx.mp4")
        cmd = ["ffmpeg", "-y"] + inputs + [
            "-filter_complex", filter_complex, "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", out_path,
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0 and os.path.exists(out_path):
            return out_path
    except Exception as e:
        print(f"[sfx] failed, using original video: {e}")
    return video_path


# ---------------------------------------------------------------------------
# Thumbnail / cover frame
# ---------------------------------------------------------------------------

def make_cover_image(video_path, outdir):
    cover_path = os.path.join(outdir, "cover.jpg")
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path, "-ss", "00:00:01.000",
        "-vframes", "1", cover_path,
    ], capture_output=True)
    return cover_path if os.path.exists(cover_path) else None


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

TITLE_PATTERNS = [
    "{topic}: The Truth Nobody Tells You",
    "This {topic} Fact Will Blow Your Mind",
    "You've Been Wrong About {topic}",
    "The Dark Side of {topic}",
    "{topic} Facts They Don't Teach You",
]

BASE_HASHTAGS = [
    "fyp", "factsdaily", "didyouknow", "learnontiktok", "factchecked",
    "interestingfacts", "mindblown", "todayilearned",
]


def generate_metadata(topic):
    title = random.choice(TITLE_PATTERNS).format(topic=topic)
    tags = [re.sub(r"[^a-zA-Z0-9]", "", topic).lower()] + BASE_HASHTAGS
    tags = list(dict.fromkeys(tags))[:15]
    caption = f"{title} #" + " #".join(tags)
    return {"title": title, "caption": caption, "tags": tags}


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def create_video(topic=None):
    history = load_history()
    topic = topic or pick_topic(history)
    outdir = get_workdir(topic)
    print(f"[video_maker] topic: {topic} -> {outdir}")

    script = generate_script_claude(topic, "shorts", config.ANTHROPIC_API_KEY)

    voice = get_best_voice()  # required: never hardcode voice
    voiceover_path = os.path.join(outdir, "voiceover.mp3")
    ok = make_voiceover(script, voice, voiceover_path)
    if not ok:
        raise RuntimeError("Voiceover generation failed (edge-tts + gtts both failed)")

    print("[video_maker] downloading stock clips from Pexels...")
    clips = fetch_pexels_clips(topic, outdir, needed_seconds=45)
    if not clips:
        raise RuntimeError("No Pexels clips found for topic or fallback queries")

    print("[video_maker] assembling video (zoom + color grade + music mix)...")
    final_video, audio_dur = assemble_video(clips, voiceover_path, outdir)

    captioned = add_captions(final_video, outdir)

    print("[video_maker] adding sound effects...")
    with_sfx = add_sound_effects(captioned, audio_dur, outdir)

    print("[video_maker] generating cover image...")
    cover = make_cover_image(with_sfx, outdir)
    metadata = generate_metadata(topic)

    history.setdefault("videos", []).append({
        "topic": topic, "path": with_sfx, "metadata": metadata,
        "timestamp": time.time(),
    })
    save_history(history)

    return {
        "video_path": with_sfx,
        "cover_path": cover,
        "metadata": metadata,
        "topic": topic,
    }


if __name__ == "__main__":
    result = create_video()
    print(json.dumps({k: v for k, v in result.items() if k != "metadata"}, indent=2))
    print("Metadata:", result["metadata"])
