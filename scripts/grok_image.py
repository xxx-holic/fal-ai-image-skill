#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "httpx>=0.27.0",
#     "pillow>=10.0.0",
# ]
# ///
"""
Grok Aurora image generation via OpenAI-compatible API.
Fallback for when fal.ai content policy blocks the request.

Usage:
  uv run grok_image.py generate -p "your prompt" -f output.png
  uv run grok_image.py edit -p "edit instruction" -i input.png -f output.png

Environment: GROK_API_BASE and GROK_API_KEY must be set.
"""

import argparse
import base64
import os
import sys
from pathlib import Path

MODELS = {
    "generate": "grok-imagine-1.0",
    "fast": "grok-imagine-1.0-fast",
    "edit": "grok-imagine-1.0-edit",
}


def get_config(args):
    base = args.api_base or os.environ.get("GROK_API_BASE")
    key = args.api_key or os.environ.get("GROK_API_KEY")
    if not base or not key:
        print("Error: Set GROK_API_BASE and GROK_API_KEY env vars or pass --api-base/--api-key.", file=sys.stderr)
        sys.exit(1)
    return base.rstrip("/"), key


def download_image(url: str, dest: Path, api_key: str):
    import httpx
    from PIL import Image as PILImage
    from io import BytesIO

    headers = {"Authorization": f"Bearer {api_key}"}
    resp = httpx.get(url, follow_redirects=True, timeout=120, headers=headers)
    resp.raise_for_status()
    img = PILImage.open(BytesIO(resp.content))
    suffix = dest.suffix.lower()
    if suffix in (".jpg", ".jpeg"):
        img.convert("RGB").save(str(dest), "JPEG", quality=95)
    else:
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.save(str(dest), "PNG")


def run_generate(base: str, key: str, args):
    import httpx

    model = MODELS["fast"] if args.fast else MODELS["generate"]
    if args.model:
        model = args.model

    size = args.size or "1024x1792"  # default to portrait
    payload = {
        "model": model,
        "prompt": args.prompt,
        "n": 1,
        "size": size,
        "response_format": "b64_json",
    }

    print(f"Generating with {model} (size={size}) ...")
    resp = httpx.post(
        f"{base}/v1/images/generations",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("data"):
        print(f"Error: No image in response.\n{data}", file=sys.stderr)
        sys.exit(1)

    # Prefer b64_json (full quality) over URL (proxy may compress URLs)
    return data["data"][0].get("b64_json") or data["data"][0].get("url")


def run_edit(base: str, key: str, args):
    import httpx

    if not args.input_image:
        print("Error: --input-image / -i required for edit mode.", file=sys.stderr)
        sys.exit(1)

    model = MODELS["edit"]
    if args.model:
        model = args.model

    # Read and base64 encode the input image
    img_path = Path(args.input_image).expanduser().resolve()
    if not img_path.exists():
        print(f"Error: Input image not found: {img_path}", file=sys.stderr)
        sys.exit(1)

    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    # Use chat completions with image for edit
    payload = {
        "model": model,
        "prompt": args.prompt,
        "n": 1,
        "image": img_b64,
    }

    print(f"Editing with {model} ...")
    resp = httpx.post(
        f"{base}/v1/images/generations",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()

    if not data.get("data"):
        print(f"Error: No image in response.\n{data}", file=sys.stderr)
        sys.exit(1)

    return data["data"][0].get("url") or data["data"][0].get("b64_json")


def main():
    parser = argparse.ArgumentParser(description="Grok Aurora image tool")
    parser.add_argument("mode", choices=["generate", "edit"], help="Operation mode")
    parser.add_argument("--prompt", "-p", required=True, help="Text prompt")
    parser.add_argument("--filename", "-f", required=True, help="Output filename")
    parser.add_argument("--input-image", "-i", default=None, dest="input_image", help="Input image for edit")
    parser.add_argument("--fast", action="store_true", help="Use fast model")
    parser.add_argument(
        "--size", "-s", default=None,
        choices=["1024x1024", "1024x1792", "1792x1024", "1280x720", "720x1280"],
        help="Image size (default: 1024x1792 for portrait)",
    )
    parser.add_argument("--model", default=None, help="Override model ID")
    parser.add_argument("--api-base", default=None, help="API base URL")
    parser.add_argument("--api-key", default=None, help="API key")
    parser.add_argument("--no-media-tag", action="store_true", help="Suppress MEDIA: line")

    args = parser.parse_args()
    base, key = get_config(args)

    output_path = Path(args.filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if args.mode == "generate":
        result = run_generate(base, key, args)
    else:
        result = run_edit(base, key, args)

    # Handle URL or base64 result
    if result and result.startswith("http"):
        print("Downloading ...")
        download_image(result, output_path, key)
    elif result:
        # base64
        from PIL import Image as PILImage
        from io import BytesIO
        img_data = base64.b64decode(result)
        img = PILImage.open(BytesIO(img_data))
        img.save(str(output_path), "PNG")
    else:
        print("Error: No image URL or data returned.", file=sys.stderr)
        sys.exit(1)

    full_path = output_path.resolve()
    print(f"\nImage saved: {full_path}")
    if not args.no_media_tag:
        print(f"MEDIA:{full_path}")


if __name__ == "__main__":
    main()
