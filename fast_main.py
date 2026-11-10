"""
fast_main.py
A faster version of main.py. Drop this file into your tiktok_bot folder
next to the others — it does NOT modify main.py, video_maker.py, or
tiktok_uploader.py at all. It just swaps in quicker versions of the slow
video-processing steps (the zoom/color-grade pass and the captions pass)
right before running the exact same pipeline main.py runs.

RUN THIS INSTEAD OF main.py:
    python fast_main.py
    python fast_main.py --schedule
    python fast_main.py "Some Topic"

What's different from the normal pipeline (speed vs a little quality):
- Zoom/color-grade pass is done at half resolution, then upscaled once at
  the very end, instead of at full 1080x1920 for every single clip. This
  is the single biggest time saver — zoompan is very CPU-heavy and its
  cost grows fast with resolution.
- Each clip covers more seconds of the video (10s instead of 6s), so
  fewer zoom passes are needed overall.
- Captions use Whisper's "tiny" model instead of "base" — noticeably
  faster transcription, very slightly less accurate on unclear audio.
- Every ffmpeg encode step here uses the "ultrafast" preset.
"""

import os
import random
import subprocess

import config
import video_maker
import tiktok_uploader
import main  # reuse its scheduling + run_once/CLI logic as-is


def fast_process_clip(clip_path, out_path, duration_target=10):
    full_w, full_h = config.TARGET_W, config.TARGET_H
    w, h = full_w // 2, full_h // 2  # process small, upscale once at the end
    n_frames = int(duration_target * 20)
    zoom_variants = [
        f"zoompan=z='min(zoom+0.0015,1.3)':d={n_frames}:s={w}x{h}",
        f"zoompan=z='if(lte(zoom,1.0),1.3,max(1.0,zoom-0.0015))':d={n_frames}:s={w}x{h}",
    ]
    zoom = random.choice(zoom_variants)
    vf = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},{zoom}"
    cmd = [
        "ffmpeg", "-y", "-i", clip_path, "-t", str(duration_target),
        "-vf", vf, "-an", "-r", "20",
        "-preset", "ultrafast", "-threads", "0",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True)


def fast_assemble_video(clip_paths, voiceover_path, outdir):
    audio_dur = video_maker.get_audio_duration(voiceover_path)
    per_clip = 10  # longer per clip = fewer zoompan passes needed
    n_needed = max(1, int(audio_dur // per_clip) + 1)

    processed = []
    i = 0
    while len(processed) < n_needed:
        src = clip_paths[i % len(clip_paths)]
        out_path = os.path.join(outdir, f"ft{len(processed)}.mp4")
        print(f"[fast-assemble] clip {len(processed) + 1}/{n_needed}...")
        fast_process_clip(src, out_path, per_clip)
        processed.append(out_path)
        i += 1

    print("[fast-assemble] concatenating...")
    concat_list = os.path.join(outdir, "fconcat_list.txt")
    with open(concat_list, "w", encoding="utf-8") as f:
        for p in processed:
            f.write(f"file '{os.path.abspath(p)}'\n")
    concat_path = os.path.join(outdir, "fconcat.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
        "-c", "copy", concat_path,
    ], capture_output=True)

    print("[fast-assemble] mixing audio...")
    bg_music = os.path.join(outdir, "fbg_music.aac")
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"sine=frequency=220:duration={audio_dur}",
        "-af", "volume=0.01", bg_music,
    ], capture_output=True)
    mixed = os.path.join(outdir, "fmixed.aac")
    subprocess.run([
        "ffmpeg", "-y", "-i", voiceover_path, "-i", bg_music,
        "-filter_complex",
        "[0:a]volume=5.0[a0];[1:a]volume=0.01[a1];[a0][a1]amix=inputs=2:duration=first:weights=50 1[aout]",
        "-map", "[aout]", mixed,
    ], capture_output=True)

    print("[fast-assemble] final mux + upscale to full size (single pass, ultrafast)...")
    final_video = os.path.join(outdir, "final_video.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-i", concat_path, "-i", mixed,
        "-vf", f"scale={config.TARGET_W}:{config.TARGET_H}",
        "-c:v", "libx264", "-preset", "ultrafast", "-threads", "0",
        "-c:a", "aac", "-shortest", final_video,
    ], capture_output=True)

    return final_video, audio_dur


def fast_add_captions(video_path, outdir):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("[fast-captions] faster-whisper not installed, skipping")
        return video_path

    print("[fast-captions] transcribing with the 'tiny' model (fast)...")
    model = WhisperModel("tiny")
    segments, _ = model.transcribe(video_path, word_timestamps=True)

    words = []
    for seg in segments:
        for w in (seg.words or []):
            words.append((w.start, w.end, w.word.strip()))
    if not words:
        print("[fast-captions] no words detected, skipping captions")
        return video_path

    print(f"[fast-captions] building filter for {len(words)} words...")
    colors = ["yellow", "white", "cyan", "orange", "green", "pink"]
    parts = []
    for idx, (start, end, word) in enumerate(words):
        safe = word.replace("'", "").replace(":", "").replace("\\", "").replace("%", "")
        if not safe:
            continue
        parts.append(
            f"drawtext=text='{safe}':fontcolor={colors[idx % len(colors)]}:fontsize=48:"
            f"x=(w-text_w)/2:y=h*0.8:enable='between(t,{start:.2f},{end:.2f})'"
        )
    if not parts:
        return video_path

    filter_file = os.path.join(outdir, "fcaptions_filter.txt")
    with open(filter_file, "w", encoding="utf-8") as f:
        f.write(",".join(parts))

    out_path = os.path.join(outdir, "final_captioned.mp4")
    print("[fast-captions] encoding (single pass, ultrafast)...")
    result = subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-filter_script:v", filter_file,
        "-preset", "ultrafast", "-threads", "0",
        "-c:a", "copy", out_path,
    ], capture_output=True)

    if result.returncode == 0 and os.path.exists(out_path):
        print("[fast-captions] done.")
        return out_path
    print("[fast-captions] failed, using video without captions.")
    return video_path


# Swap the slow functions inside video_maker for these faster ones. Because
# create_video() looks up process_clip/assemble_video/add_captions by name
# at call time (normal Python behavior), this affects it without editing
# video_maker.py at all.
video_maker.process_clip = fast_process_clip
video_maker.assemble_video = fast_assemble_video
video_maker.add_captions = fast_add_captions


if __name__ == "__main__":
    main.main()
