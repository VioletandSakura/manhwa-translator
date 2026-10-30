"""Generate original Japanese comic fixtures and record actual OCR limitations.

No user manga, system credentials or paid translation is used. These fixtures
cover layout cases; they are not a benchmark of representative published manga.
"""
from __future__ import annotations

import argparse
import asyncio
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw, ImageFont


def make_samples(target: Path) -> list[dict]:
    target.mkdir(parents=True, exist_ok=True)
    font_path = Path("C:/Windows/Fonts/msgothic.ttc")
    if not font_path.is_file():
        raise RuntimeError("Japanese fixture generation needs the Windows MS Gothic font.")
    font = ImageFont.truetype(str(font_path), 38)
    cases = [
        ("horizontal", ["こんにちは！", "今日は一緒に学校へ行こう。"]),
        ("vertical", ["ちょっと待って！", "その扉を開けないで。"]),
        ("textured", ["ありがとう。", "また明日会おうね。", "ドーン"]),
    ]
    manifest = []
    for name, lines in cases:
        image = Image.new("RGB", (960, 1280), "#fafafa")
        draw = ImageDraw.Draw(image)
        for top in (30, 650):
            draw.rectangle((30, top, 930, top + 590), outline="black", width=4)
            # Original minimal character, with no third-party art.
            draw.ellipse((140, top + 300, 300, top + 460), fill="#eeeeee", outline="black", width=3)
            draw.arc((115, top + 275, 325, top + 470), 180, 360, fill="black", width=7)
            draw.ellipse((180, top + 365, 186, top + 375), fill="black")
            draw.ellipse((248, top + 365, 254, top + 375), fill="black")
            draw.arc((200, top + 395, 238, top + 420), 0, 180, fill="black", width=2)
            draw.line((220, top + 460, 155, top + 565), fill="black", width=3)
            draw.line((220, top + 460, 300, top + 565), fill="black", width=3)
        if name == "textured":
            for x in range(35, 930, 16):
                draw.line((x, 655, min(x + 180, 925), 1235), fill="#c4c4c4", width=2)
        for index, line in enumerate(lines[:2]):
            top = 45 + index * 620
            if name == "vertical":
                draw.ellipse((520, top + 40, 850, top + 520), fill="white", outline="black", width=3)
                for offset, character in enumerate(line):
                    column, row = divmod(offset, 7)
                    draw.text((755 - column * 58, top + 95 + row * 46), character, font=font, fill="black")
            else:
                draw.ellipse((115, top + 60, 860, top + 260), fill="white", outline="black", width=3)
                draw.text((487, top + 160), line, font=font, fill="black", anchor="mm")
        if name == "textured":
            draw.text((600, 1150), lines[2], font=ImageFont.truetype(str(font_path), 64), fill="black", anchor="mm")
        path = target / f"{name}.png"
        image.save(path)
        manifest.append({"file": path.name, "expected_texts": lines,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    (target / "expected.json").write_text(json.dumps({
        "license": "MIT", "artwork": "Original generated fixtures, VioletandSakura and contributors",
        "font_file_distributed": False, "cases": manifest,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def normalize(text: str) -> str:
    return re.sub(r"[\W_]", "", unicodedata.normalize("NFKC", text))


async def evaluate(samples: Path, report_root: Path, manifest: list[dict]) -> dict:
    from app.paths import AppPaths
    from app.security.credentials import CredentialStore
    from app.services.application import TranslationApplication
    from app.tasks.pipeline import PipelineOptions

    class NoCredentials:
        def get_password(self, *_args):
            return None

    application = TranslationApplication(AppPaths(report_root / "runtime", report_root / "results", report_root / "temp"),
                                         credentials=CredentialStore(NoCredentials()))
    application.initialize()
    pipeline = application.build_pipeline(translate=False)
    results = []
    try:
        for item in manifest:
            source = samples / item["file"]
            result = await pipeline.process(source, report_root / "results", PipelineOptions(translate=False))
            document = json.loads(result.artifacts.metadata.read_text(encoding="utf-8"))
            found = [r["source_text"] for r in document["regions"] if r.get("source_text")]
            expected = item["expected_texts"]
            # This fuzzy score is diagnostic: fragments and reading order may
            # differ, so it is not character accuracy or a pass/fail threshold.
            matches = [{"expected": text, "best_similarity": round(max(
                (SequenceMatcher(None, normalize(text), normalize(actual)).ratio() for actual in found), default=0), 3)}
                for text in expected]
            with Image.open(source) as original, Image.open(result.artifacts.translated) as output:
                unchanged = original.convert("RGB").tobytes() == output.convert("RGB").tobytes()
            if not unchanged:
                raise AssertionError("Recognition-only output changed source pixels")
            results.append({**item, "recognized_texts": found, "regions": len(document["regions"]),
                            "needs_review": sum(bool(r.get("needs_review")) for r in document["regions"]),
                            "diagnostic_matches": matches, "ocr_only_pixels_unchanged": unchanged})
    finally:
        application.database.close()
    return {"mode": "generated original Japanese layout fixtures", "paid_api_requests": 0,
            "representative_manga_quality_claimed": False, "cases": results}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples-dir", type=Path, default=ROOT / "docs/samples")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--generate-only", action="store_true")
    args = parser.parse_args()
    if not args.generate_only and args.output_dir is None:
        parser.error("--output-dir is required unless --generate-only is selected")
    if args.output_dir and args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("Use a new output directory to preserve previous evidence")
    manifest = make_samples(args.samples_dir)
    if args.generate_only:
        print(f"Generated {len(manifest)} original fixtures")
        return 0
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(evaluate(args.samples_dir, args.output_dir, manifest))
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"cases": len(report["cases"]), "paid_api_requests": 0, "report": str(args.output_dir / "report.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
