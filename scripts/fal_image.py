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
  uv run fal_image.py generate -p "a cat on the moon" -f out.png
  uv run fal_image.py edit -p "make sky purple" -i photo.png -f out.png
  uv run fal_image.py edit -p "replace phone with ref" -i target.png --ref ref.png -f out.png
  uv run fal_image.py inpaint -p "fill with grass" -i photo.png -m mask.png -f out.png
  uv run fal_image.py upscale -i photo.png -f out_4x.png
  uv run fal_image.py rembg -i photo.png -f out_nobg.png

Environment: FAL_KEY must be set.
"""

import argparse
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Model registry
# ---------------------------------------------------------------------------
MODELS = {
    "generate": "fal-ai/flux-pro/v1.1-ultra",
    "edit":     "fal-ai/flux-pro/kontext/max",
    "edit_multi": "fal-ai/flux-2-pro/edit",
    "inpaint":  "fal-ai/flux-pro/v1/fill",
    "upscale":  "fal-ai/esrgan",
    "rembg":    "fal-ai/birefnet",
}

ASPECT_RATIOS = [
    "1:1", "2:3", "3:2", "3:4", "4:3",
    "4:5", "5:4", "9:16", "16:9", "21:9",
]

MAX_RETRIES = 2
RETRY_DELAY = 3  # seconds


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
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
    print(f"Uploading {p.name} ...")
    url = fal_client.upload_file(str(p))
    print(f"  → {url}")
    return url


def inject_common_params(params: dict, args) -> dict:
    """Inject shared generation params into a request dict."""
    if getattr(args, "seed", None) is not None:
        params["seed"] = args.seed
    if getattr(args, "guidance_scale", None) is not None:
        params["guidance_scale"] = args.guidance_scale
    if getattr(args, "num_inference_steps", None) is not None:
        params["num_inference_steps"] = args.num_inference_steps
    if getattr(args, "safety_tolerance", None) is not None:
        params["safety_tolerance"] = args.safety_tolerance
    if getattr(args, "negative_prompt", None):
        params["negative_prompt"] = args.negative_prompt
    if getattr(args, "output_format", None):
        params["output_format"] = args.output_format
    if getattr(args, "enhance_prompt", False):
        params["enhance_prompt"] = True
    return params


def call_with_retry(fal_client, model: str, params: dict, retries: int = MAX_RETRIES) -> dict:
    """Call fal API with retry on transient errors."""
    last_err = None
    for attempt in range(1, retries + 2):
        try:
            result = fal_client.subscribe(model, arguments=params, with_logs=True)
            return result
        except Exception as e:
            last_err = e
            err_str = str(e).lower()
            # Don't retry on auth / validation errors
            if any(k in err_str for k in ("unauthorized", "invalid", "forbidden", "not found", "api key")):
                raise
            if attempt <= retries:
                print(f"  ⚠ Attempt {attempt} failed: {e}")
                print(f"  Retrying in {RETRY_DELAY}s ...")
                time.sleep(RETRY_DELAY)
            else:
                raise last_err


def download_image(url: str, dest: Path, force_png: bool = False):
    """Download image from URL. Preserves format unless force_png."""
    import httpx
    from PIL import Image as PILImage
    from io import BytesIO

    resp = httpx.get(url, follow_redirects=True, timeout=120)
    resp.raise_for_status()

    img = PILImage.open(BytesIO(resp.content))
    suffix = dest.suffix.lower()

    if suffix in (".jpg", ".jpeg") and not force_png:
        img.convert("RGB").save(str(dest), "JPEG", quality=95)
    elif suffix == ".webp":
        img.save(str(dest), "WEBP", quality=95)
    else:
        # Default to PNG
        if img.mode == "RGBA" and suffix != ".png":
            bg = PILImage.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            bg.save(str(dest), "PNG")
        else:
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            img.save(str(dest), "PNG")


def extract_image_url(result: dict) -> str | None:
    """Extract the first image URL from various fal response formats."""
    for key_path in [
        lambda r: r.get("images", [{}])[0].get("url") if r.get("images") else None,
        lambda r: r.get("image", {}).get("url") if isinstance(r.get("image"), dict) else None,
        lambda r: r.get("data", {}).get("image", {}).get("url") if isinstance(r.get("data"), dict) else None,
        lambda r: r.get("output", {}).get("url") if isinstance(r.get("output"), dict) else None,
    ]:
        try:
            url = key_path(result)
            if url:
                return url
        except (IndexError, KeyError, TypeError, AttributeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Mode handlers
# ---------------------------------------------------------------------------
def run_generate(fal_client, args):
    params: dict = {"prompt": args.prompt}
    if args.aspect_ratio:
        params["aspect_ratio"] = args.aspect_ratio
    inject_common_params(params, args)

    model = MODELS["generate"]
    print(f"Generating with {model} ...")
    return call_with_retry(fal_client, model, params)


def run_edit(fal_client, args):
    if not args.input_images:
        print("Error: --input-image / -i required for edit mode.", file=sys.stderr)
        sys.exit(1)

    # Collect all images: main input(s) + reference(s)
    all_images = list(args.input_images)
    if args.ref_images:
        all_images.extend(args.ref_images)

    img_url = upload_image(fal_client, all_images[0])
    params: dict = {"prompt": args.prompt, "image_url": img_url}
    inject_common_params(params, args)

    if len(all_images) == 1:
        # Single image → kontext/max (best precision for local edits)
        model = MODELS["edit"]
    else:
        # Multiple images → flux-2-pro/edit (supports up to 9 reference images)
        model = MODELS["edit_multi"]
        extra_urls = [upload_image(fal_client, p) for p in all_images[1:]]
        params.pop("image_url", None)
        params["image_urls"] = [img_url] + extra_urls

    print(f"Editing with {model} ({len(all_images)} image{'s' if len(all_images) > 1 else ''}) ...")
    return call_with_retry(fal_client, model, params)


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
    inject_common_params(params, args)

    model = MODELS["inpaint"]
    print(f"Inpainting with {model} ...")
    return call_with_retry(fal_client, model, params)


def run_upscale(fal_client, args):
    if not args.input_images:
        print("Error: --input-image / -i required for upscale mode.", file=sys.stderr)
        sys.exit(1)

    img_url = upload_image(fal_client, args.input_images[0])
    params: dict = {"image_url": img_url}
    if args.scale:
        params["scale"] = args.scale

    model = MODELS["upscale"]
    print(f"Upscaling with {model} ...")
    return call_with_retry(fal_client, model, params)


def run_rembg(fal_client, args):
    if not args.input_images:
        print("Error: --input-image / -i required for rembg mode.", file=sys.stderr)
        sys.exit(1)

    img_url = upload_image(fal_client, args.input_images[0])
    params: dict = {"image_url": img_url}

    model = MODELS["rembg"]
    print(f"Removing background with {model} ...")
    return call_with_retry(fal_client, model, params)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="fal.ai image tool — generate / edit / inpaint / upscale / rembg"
    )
    parser.add_argument("mode", choices=["generate", "edit", "inpaint", "upscale", "rembg"])
    parser.add_argument("--prompt", "-p", default=None, help="Text prompt")
    parser.add_argument("--filename", "-f", required=True, help="Output filename")
    parser.add_argument(
        "--input-image", "-i", action="append", dest="input_images",
        metavar="IMG", help="Input image(s). Repeat -i for multiple.",
    )
    parser.add_argument(
        "--ref", "--reference", action="append", dest="ref_images",
        metavar="IMG", help="Reference image(s) for edit mode. Repeat --ref for multiple.",
    )
    parser.add_argument("--mask", "-m", default=None, help="Mask image for inpaint")
    parser.add_argument(
        "--aspect-ratio", "-a", choices=ASPECT_RATIOS, default=None,
        help="Aspect ratio (generate only)",
    )
    parser.add_argument("--seed", "-s", type=int, default=None, help="Random seed")
    parser.add_argument("--scale", type=int, default=None, help="Upscale factor (2/4)")
    parser.add_argument(
        "--guidance-scale", "-g", type=float, default=None,
        help="Prompt adherence 0-20 (e.g. 3.5)",
    )
    parser.add_argument(
        "--num-inference-steps", "-n", type=int, default=None,
        help="Inference steps (more = slower + better)",
    )
    parser.add_argument(
        "--safety-tolerance", type=int, default=5, choices=range(0, 6),
        metavar="0-5", help="Safety filter 0(strict)-5(permissive), default 5",
    )
    parser.add_argument(
        "--negative-prompt", default=None,
        help="What to avoid in the output",
    )
    parser.add_argument(
        "--output-format", choices=["png", "jpeg"], default=None,
        help="Output format hint to API",
    )
    parser.add_argument(
        "--enhance-prompt", action="store_true",
        help="Let the model enhance your prompt",
    )
    parser.add_argument("--api-key", "-k", default=None, help="FAL_KEY override")
    parser.add_argument("--model", default=None, help="Override default model for this mode")
    parser.add_argument(
        "--no-media-tag", action="store_true",
        help="Suppress MEDIA: line (caller handles delivery)",
    )
    parser.add_argument("--debug", action="store_true", help="Print full API response")

    args = parser.parse_args()

    # Validate
    if args.mode in ("generate", "edit") and not args.prompt:
        print(f"Error: --prompt required for {args.mode} mode.", file=sys.stderr)
        sys.exit(1)

    # Setup
    api_key = get_api_key(args.api_key)
    os.environ["FAL_KEY"] = api_key

    import fal_client

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

    if args.debug:
        import json
        print(f"\n[DEBUG] Full API response:\n{json.dumps(result, indent=2, default=str)}")

    # Extract and download
    image_url = extract_image_url(result)
    if not image_url:
        import json
        print(f"Error: No image in response.\n{json.dumps(result, indent=2, default=str)}", file=sys.stderr)
        sys.exit(1)

    print(f"Downloading ...")
    download_image(image_url, output_path)

    full_path = output_path.resolve()
    print(f"\nImage saved: {full_path}")
    if not args.no_media_tag:
        print(f"MEDIA:{full_path}")


if __name__ == "__main__":
    main()
