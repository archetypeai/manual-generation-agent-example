#!/usr/bin/env python3
"""Fetch the serving videos from YouTube into data/clips/ and data/clips-more/.

    python3 fetch_videos.py              # every video not already here
    python3 fetch_videos.py --list       # what, from where, and the md5 to expect
    python3 fetch_videos.py pickles jello-shots

Videos are copyrighted by their uploaders, so they are fetched, never committed
(data/ is gitignored). The YouTube ids and yt-dlp formats are the ones
manual-gen-with-manual-generation-agent's scripts/fetch-sample-videos.mjs uses,
except the tire clip, which keeps run_mga_example.py's 480p format. Each file is
checked against the md5 of the copy the README's results came from. A mismatch is
reported, not fatal: YouTube re-encodes now and then, and the chunks cut from a
different rendition differ slightly too.

tire_i2JWkDyg26A-silent is the tire clip with its audio stripped. mouse_assembly is
an internal clip, not on YouTube: ask for it.

Needs yt-dlp (keep it current; an old one gets HTTP 403) and ffmpeg. Stdlib only.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys

F480 = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
F720 = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
# french-toast: F480 now picks a VP9 rendition; the measured copy is H.264 360p (134) + AAC (140).

# (folder, name, YouTube id, format, md5 of the copy measured on 2026-10-27)
VIDEOS = [
    ("data/clips", "tire_i2JWkDyg26A", "i2JWkDyg26A", F480, "5771f46d637314fdad86d6c4c934dbb5"),
    ("data/clips", "french-toast", "aVHhIDjZZk8", "134+140/" + F480, "729ff03b67a0fe29d2f58c82def810fa"),
    ("data/clips", "make-a-latte", "HYKNx9Fd7I8", F720, "537df49594d8f65118eff6b180dcbb41"),
    # YouTube re-encoded this one's AV1 stream after it was measured: expect an md5 mismatch.
    ("data/clips", "jack-up-a-car", "X2XdxPJnpNE", F720, "a90760bce8a7ba24348bda8dbccfaa79"),
    ("data/clips-more", "rotate-tires", "4Js8FycNYQE", F720, "ea86f8ff5b9065619b7322f28084cbca"),
    ("data/clips-more", "check-engine-oil", "uePUThh2vhk", F720, "0759d749e3714202f989ba2b585b9c1d"),
    ("data/clips-more", "jello-shots", "7DRkU_dBL1k", F720, "3f52d8a7137b3e1d3de9f2467a31b7fc"),
    ("data/clips-more", "pickles", "X2FuVybCHDc", F720, "de8615898d4d1aac0cf6312eea14d066"),
    ("data/clips-more", "change-a-tire-long", "zlyrv-h97z8", F720, "2f9973b81a31554fac0256876ddd878f"),
    ("data/clips-more", "oil-change-long", "90XGGyrgELs", F720, "306097ccc4193e70e702b943418c52fd"),
    ("data/clips-more", "floating-shelf-long", "aW9TAdTt_5g", F720, "39207706ad30739077c801ce0e7da842"),
]
SILENT = ("data/clips/tire_i2JWkDyg26A.mp4", "data/clips/tire_i2JWkDyg26A-silent.mp4",
          "4abee1733e2471c566575de932e56ffb")
NOT_ON_YOUTUBE = "data/clips/mouse_assembly.mp4"


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check(path: str, expected: str) -> None:
    got = md5(path)
    print(f"  {'md5 ok' if got == expected else f'md5 {got[:8]}… differs from {expected[:8]}…'}  {path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="only these videos (default: all)")
    ap.add_argument("--list", action="store_true", help="print the list and stop")
    args = ap.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    if args.list:
        for folder, name, vid, _, expected in VIDEOS:
            print(f"{folder}/{name}.mp4".ljust(42) + f"https://youtu.be/{vid}  md5 {expected}")
        print(f"{SILENT[1]}".ljust(42) + "the tire clip, audio stripped")
        print(f"{NOT_ON_YOUTUBE}".ljust(42) + "internal, not on YouTube: ask for it")
        return

    for tool in ("yt-dlp", "ffmpeg"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found (brew install {tool}). yt-dlp needs ffmpeg to merge video and audio.")
    unknown = set(args.names) - {v[1] for v in VIDEOS} - {"tire_i2JWkDyg26A-silent"}
    if unknown:
        sys.exit(f"unknown: {', '.join(sorted(unknown))}. See --list.")

    failed = []
    for folder, name, vid, fmt, expected in VIDEOS:
        if args.names and name not in args.names:
            continue
        path = os.path.join(folder, f"{name}.mp4")
        if not os.path.exists(path):
            os.makedirs(folder, exist_ok=True)
            print(f"fetching {path} from https://youtu.be/{vid}")
            subprocess.run(["yt-dlp", "--no-update", "--quiet", "-f", fmt, "--merge-output-format", "mp4",
                            "-o", path, f"https://www.youtube.com/watch?v={vid}"])
            # yt-dlp can exit 0 without a merged file; the file is the test.
            if not os.path.exists(path):
                print(f"  FAILED: no {path} (a 403 is YouTube rate-limiting; update yt-dlp and retry)")
                failed.append(name)
                continue
        check(path, expected)

    src, dst, expected = SILENT
    if (not args.names or "tire_i2JWkDyg26A-silent" in args.names) and os.path.exists(src):
        if not os.path.exists(dst):
            print(f"stripping the audio: {dst}")
            subprocess.run(["ffmpeg", "-v", "error", "-i", src, "-an", "-c:v", "copy", dst], check=True)
        check(dst, expected)
    if not args.names and not os.path.exists(NOT_ON_YOUTUBE):
        print(f"missing {NOT_ON_YOUTUBE}: internal, not on YouTube. Ask for it, or leave it out.")
    if failed:
        sys.exit(f"{len(failed)} failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
