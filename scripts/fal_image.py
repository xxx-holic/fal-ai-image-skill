#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "fal-client>=0.5.0",
#     "httpx>=0.27.0",
#     "pillow>=10.0.0",
# ]
# ///
"""
fal.ai unified image tool — generate, edit, inpaint, upscale, remove-bg.

Usage:
  # Generate
  uv run fal_image.py generate --prompt "a cat on the moon" --filename out.png

  # Edit (Kontext)
  uv run fal_image.py edit --prompt "make the sky purple" -i photo.png --filename out.png

  # Inpaint
  uv run fal_image.py inpaint --prompt "fill with grass" -i photo.png --mask mask.png --filename out.png

  # Upscale
  uv run fal_image.py upscale -i photo.png --filename out_4x.png

  # Remove background
  uv run fal_image.py rembg -i photo.png --filename out_nobg.png

Environment: FAL_KEY must be set.
"""

import argparse
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Model registry — easy to swap / extend
# ---------------------------------------------------------------------------
MODELS = {
    "generate": "fal-ai/flux-pro/v1.1-ultra",
    "edit": "fal-ai/flux-pro/kontext",
    "inpaint": "fal-ai/flux-pro/v1/fill",
    "upscale": "fal-ai/esrgan",
    "rembg": "fal-ai/birefnet",
}

ASPECT_RATIOS = [
    "1:1", "2:3", "3:2", "3:4", "4:3",
    "4:5", "5:4", "9:16", "16:9", "21:9",
]


def get_api_key(provided: str | None) -> str:
    key = provided or os.environ.get("FAL_KEY")
    if not key:
        print("Error: No API key. Set FAL_KEY env var or pass --api-key.", file=sys.stderr)
        sys.exit(1)
    return key


def upload_image(fal_client, path: str) -> str:
    """Upload a local image to fal CDN and return the URL."""
    p = Path(path).expanduser().resolve()
    if not p.exists():
        print(f"Error: Input image not found: {p}", file=sys.stderr)
        sys.exit(1)
    print(f"Uploading {p} ...")
    url = fal_client.upload_file(str(p))
    print(f"Uploaded → {url}")
    return url


def download_image(url: str, dest: Path):
    """Download an image from URL and save as PNG."""
    import httpx
    from PIL import Image as PILImage
    from io import BytesIO

    resp = httpx.get(url, follow_redirects=True, timeout=120)
    resp.raise_for_status()
    img = PILImage.open(BytesIO(resp.content))
    if img.mode == "RGBA":
        bg = PILImage.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[3])
        bg.save(str(dest), "PNG")
    else:
        img.convert("RGB").save(str(dest), "PNG")


def run_generate(fal_client, args):
    params: dict = {"prompt": args.prompt}
    if args.aspect_ratio:
        params["aspect_ratio"] = args.aspect_ratio
    if args.seed is not None:
        params["seed"] = args.seed

    print(f"Generating with {MODELS['generate']} ...")
    result = fal_client.subscribe(
        MODELS["generate"],
        arguments=params,
        with_logs=True,
    )
    return result


def run_edit(fal_client, args):
    if not args.input_images:
        print("Error: --input-image / -i required for edit mode.", file=sys.stderr)
        sys.exit(1)
    img_url = upload_image(fal_client, args.input_images[0])
    params: dict = {
        "prompt": args.prompt,
        "image_url": img_url,
    }
    if args.seed is not None:
        params["seed"] = args.seed

    model = MODELS["edit"]
    # If multiple images, use flux-2-pro/edit which supports up to 9
    if len(args.input_images) > 1:
        model = "fal-ai/flux-2-pro/edit"
        urls = [img_url] + [upload_image(fal_client, p) for p in args.input_images[1:]]
        params.pop("image_url", None)
        params["image_urls"] = urls

    print(f"Editing with {model} ...")
    result = fal_client.subscribe(model, arguments=params, with_logs=True)
    return result


def run_inpaint(fal_client, args):
    if not args.input_images:
        print("Error: --input-image / -i required for inpaint mode.", file=sys.stderr)
        sys.exit(1)
    if not args.mask:
        print("Error: --mask required for inpaint mode.", file=sys.stderr)
        sys.exit(1)
    img_url = upload_image(fal_client, args.input_images[0])
    mask_url = upload_image(fal_client, args.mask)
    params: dict = {
        "prompt": args.prompt or "",
        "image_url": img_url,
        "mask_url": mask_url,
    }
    if args.seed is not None:
        params["seed"] = args.seed

    print(f"Inpainting with {MODELS['inpaint']} ...")
    result = fal_client.subscribe(MODELS["inpaint"], arguments=params, with_logs=True)
    return result


def run_upscale(fal_client, args):
    if not args.input_images:
        print("Error: --input-image / -i required for upscale mode.", file=sys.stderr)
        sys.exit(1)
    img_url = upload_image(fal_client, args.input_images[0])
    params: dict = {"image_url": img_url}
    if args.scale:
        params["scale"] = args.scale

    print(f"Upscaling with {MODELS['upscale']} ...")
    result = fal_client.subscribe(MODELS["upscale"], arguments=params, with_logs=True)
    return result


def run_rembg(fal_client, args):
    if not args.input_images:
        print("Error: --input-image / -i required for rembg mode.", file=sys.stderr)
        sys.exit(1)
    img_url = upload_image(fal_client, args.input_images[0])
    params: dict = {"image_url": img_url}

    print(f"Removing background with {MODELS['rembg']} ...")
    result = fal_client.subscribe(MODELS["rembg"], arguments=params, with_logs=True)
    return result


def extract_image_url(result: dict) -> str | None:
    """Extract the first image URL from various fal response formats."""
    # Format 1: {"images": [{"url": "..."}]}
    if "images" in result and result["images"]:
        return result["images"][0].get("url")
    # Format 2: {"image": {"url": "..."}}
    if "image" in result and isinstance(result["image"], dict):
        return result["image"].get("url")
    # Format 3: {"data": {"image": {"url": "..."}}}
    if "data" in result and isinstance(result["data"], dict):
        data = result["data"]
        if "image" in data and isinstance(data["image"], dict):
            return data["image"].get("url")
    # Format 4: {"output": {"url": "..."}}
    if "output" in result and isinstance(result["output"], dict):
        return result["output"].get("url")
    return None


def main():
    parser = argparse.ArgumentParser(description="fal.ai image tool")
    parser.add_argument(
        "mode",
        choices=["generate", "edit", "inpaint", "upscale", "rembg"],
        help="Operation mode",
    )
    parser.add_argument("--prompt", "-p", default=None, help="Text prompt")
    parser.add_argument(
        "--filename", "-f", required=True, help="Output filename"
    )
    parser.add_argument(
        "--input-image", "-i", action="append", dest="input_images",
        metavar="IMAGE", help="Input image(s). Repeat -i for multiple.",
    )
    parser.add_argument("--mask", "-m", default=None, help="Mask image for inpaint")
    parser.add_argument(
        "--aspect-ratio", "-a", choices=ASPECT_RATIOS, default=None,
        help="Aspect ratio for generation",
    )
    parser.add_argument("--seed", "-s", type=int, default=None, help="Random seed")
    parser.add_argument("--scale", type=int, default=None, help="Upscale factor (2/4)")
    parser.add_argument("--api-key", "-k", default=None, help="FAL_KEY override")
    parser.add_argument(
        "--model", default=None,
        help="Override default model ID for this mode",
    )

    args = parser.parse_args()

    # Validate prompt requirement
    if args.mode in ("generate", "edit") and not args.prompt:
        print(f"Error: --prompt required for {args.mode} mode.", file=sys.stderr)
        sys.exit(1)

    # Setup
    api_key = get_api_key(args.api_key)
    os.environ["FAL_KEY"] = api_key

    import fal_client

    # Override model if requested
    if args.model:
        MODELS[args.mode] = args.model

    output_path = Path(args.filename)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Dispatch
    dispatch = {
        "generate": run_generate,
        "edit": run_edit,
        "inpaint": run_inpaint,
        "upscale": run_upscale,
        "rembg": run_rembg,
    }
    try:
        result = dispatch[args.mode](fal_client, args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract and download image
    image_url = extract_image_url(result)
    if not image_url:
        print(f"Error: No image in response.\nFull result: {result}", file=sys.stderr)
        sys.exit(1)

    print(f"Downloading result ...")
    download_image(image_url, output_path)

    full_path = output_path.resolve()
    print(f"\nImage saved: {full_path}")
    print(f"MEDIA:{full_path}")


if __name__ == "__main__":
    main()
