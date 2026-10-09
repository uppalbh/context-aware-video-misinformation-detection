import json
import math
import subprocess
from fractions import Fraction
from pathlib import Path

from app.config import Settings
from app.errors import ProcessingError


def run(args: list[str], timeout: int) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        raise ProcessingError("media_tools_missing", "Install FFmpeg and ffprobe on the server.")
    except subprocess.TimeoutExpired:
        raise ProcessingError("media_timeout", "Media processing exceeded the time limit.")
    if result.returncode:
        # Never expose stderr: it may contain paths or hostile media metadata.
        raise ProcessingError(
            "invalid_media", "The clip could not be decoded. Upload a valid MP4/MOV."
        )
    return result.stdout


def inspect(path: Path, cfg: Settings) -> dict:
    try:
        probe = json.loads(
            run(
                [
                    cfg.ffprobe,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file",
                    "-show_format",
                    "-show_entries",
                    "format=format_name,duration,start_time:stream=codec_type,width,height,avg_frame_rate,duration,start_time,sample_rate,channels:stream_disposition=attached_pic",
                    "-of",
                    "json",
                    str(path),
                ],
                cfg.media_timeout,
            )
        )
        fmt = probe["format"]
        if not {"mov", "mp4"}.intersection(fmt["format_name"].split(",")):
            raise ProcessingError("unsupported_format", "Only MP4/MOV containers are supported.")
        video = next(
            s
            for s in probe["streams"]
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        )
        duration = float(fmt["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
        if duration > cfg.max_duration:
            raise ProcessingError("duration_limit", f"Clip exceeds {cfg.max_duration:g} seconds.")
        audio = next((s for s in probe["streams"] if s.get("codec_type") == "audio"), None)
        if audio is None:
            raise ProcessingError("no_audio", "This clip has no audio track to transcribe.")
        width, height = int(video["width"]), int(video["height"])
        fps = float(Fraction(video.get("avg_frame_rate", "0")))
        if not (0 < width <= 7680 and 0 < height <= 4320 and 0 < fps <= 240):
            raise ProcessingError(
                "media_limits", "Video dimensions or frame rate exceed supported limits."
            )
        audio_duration = float(audio.get("duration", duration))
        timeline_start = float(fmt.get("start_time", 0))
        audio_start = float(audio.get("start_time", timeline_start)) - timeline_start
        if not math.isfinite(timeline_start) or not math.isfinite(audio_start):
            raise ValueError()
        if not math.isfinite(audio_duration) or audio_duration <= 0:
            raise ValueError()
        return {
            "duration": duration,
            "frame_rate": fps,
            "width": width,
            "height": height,
            "audio_duration": audio_duration,
            "sample_rate": int(audio["sample_rate"]),
            "channels": int(audio["channels"]),
            "audio_start_seconds": audio_start,
            "timeline_origin": "container_start",
        }
    except (KeyError, ValueError, StopIteration, TypeError, ZeroDivisionError):
        raise ProcessingError("invalid_media", "The clip has invalid or missing media metadata.")


def extract(path: Path, output: Path, cfg: Settings):
    run(
        [
            cfg.ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-threads",
            "1",
            "-protocol_whitelist",
            "file",
            "-copyts",
            "-start_at_zero",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            "aresample=async=1:first_pts=0",
            "-t",
            str(cfg.max_duration),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-threads",
            "1",
            "-fs",
            "24000000",
            str(output),
        ],
        cfg.media_timeout,
    )
    if not output.exists() or output.stat().st_size <= 44:
        raise ProcessingError("empty_audio", "Audio extraction produced no usable audio.")
    # Reject instead of sending a silently truncated WAV to the provider.
    if output.stat().st_size >= 23_900_000:
        raise ProcessingError(
            "audio_limit", "Extracted audio exceeds the transcription size limit."
        )
