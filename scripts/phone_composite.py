#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=10.0.0",
#     "numpy>=1.26.0",
#     "fal-client>=0.5.0",
#     "httpx>=0.27.0",
# ]
# ///
"""
Composite a reference phone onto a selfie image.
Step 1: Remove background from reference phone using fal.ai birefnet
Step 2: Detect/use specified phone position in selfie
Step 3: Resize, rotate, and paste the phone with alpha blending

Usage:
  uv run phone_composite.py \
    --selfie selfie.jpg \
    --phone phone_ref.jpg \
    --bbox "x1,y1,x2,y2" \
    --rotation DEGREES \
    --output result.png
"""

import argparse
import os
import sys
from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageEnhance


def remove_background(phone_path: str) -> Image.Image:
    """Remove background from phone image using fal.ai birefnet."""
    import fal_client
    import httpx

    print("Removing phone background via fal.ai/birefnet ...")
    url = fal_client.upload_file(phone_path)
    result = fal_client.subscribe(
        "fal-ai/birefnet",
        arguments={"image_url": url},
        with_logs=True,
    )

    # Extract image URL from result
    image_url = None
    if "image" in result and isinstance(result["image"], dict):
        image_url = result["image"].get("url")
    elif "images" in result and result["images"]:
        image_url = result["images"][0].get("url")

    if not image_url:
        print(f"Error: No image in rembg response: {result}", file=sys.stderr)
        sys.exit(1)

    resp = httpx.get(image_url, follow_redirects=True, timeout=60)
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGBA")


def composite_phone(
    selfie: Image.Image,
    phone_cutout: Image.Image,
    bbox: tuple[int, int, int, int],
    rotation: float = 0,
    feather: int = 3,
    brightness: float = 1.0,
) -> Image.Image:
    """Paste phone_cutout onto selfie at bbox with rotation and edge feathering."""

    x1, y1, x2, y2 = bbox
    target_w = x2 - x1
    target_h = y2 - y1

    # Resize phone to target dimensions
    phone = phone_cutout.resize((target_w, target_h), Image.LANCZOS)

    # Adjust brightness to match scene lighting
    if brightness != 1.0:
        enhancer = ImageEnhance.Brightness(phone)
        phone = enhancer.enhance(brightness)

    # Rotate if needed (expand=True to avoid clipping, then re-crop)
    if rotation != 0:
        # Rotate with expand, then resize back to target
        phone = phone.rotate(rotation, resample=Image.BICUBIC, expand=True)
        # After rotation the size changes, resize to fit bbox
        phone = phone.resize((target_w, target_h), Image.LANCZOS)

    # Feather the edges of the alpha mask for smoother blending
    if feather > 0:
        r, g, b, a = phone.split()
        # Erode then blur the alpha mask
        a_np = np.array(a)
        # Simple erosion by setting edge pixels
        kernel_size = feather
        a_blurred = Image.fromarray(a_np).filter(
            ImageFilter.GaussianBlur(radius=kernel_size)
        )
        phone = Image.merge("RGBA", (r, g, b, a_blurred))

    # Composite
    result = selfie.copy().convert("RGBA")
    # Create a temporary layer
    layer = Image.new("RGBA", result.size, (0, 0, 0, 0))
    layer.paste(phone, (x1, y1))
    result = Image.alpha_composite(result, layer)

    return result.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="Composite phone onto selfie")
    parser.add_argument("--selfie", required=True, help="Selfie image path")
    parser.add_argument("--phone", required=True, help="Reference phone image path")
    parser.add_argument("--output", "-o", required=True, help="Output path")
    parser.add_argument(
        "--bbox", required=True,
        help="Phone bounding box in selfie: x1,y1,x2,y2 (pixels)",
    )
    parser.add_argument("--rotation", type=float, default=0, help="Rotation degrees (CW)")
    parser.add_argument("--feather", type=int, default=3, help="Edge feather radius")
    parser.add_argument("--brightness", type=float, default=1.0, help="Phone brightness adjustment")
    parser.add_argument("--skip-rembg", action="store_true", help="Phone image already has transparent bg")
    parser.add_argument("--no-media-tag", action="store_true", help="Suppress MEDIA: line")

    args = parser.parse_args()

    # Parse bbox
    try:
        bbox = tuple(int(x.strip()) for x in args.bbox.split(","))
        assert len(bbox) == 4
    except Exception:
        print("Error: --bbox must be x1,y1,x2,y2 (integers)", file=sys.stderr)
        sys.exit(1)

    # Load selfie
    selfie = Image.open(args.selfie).convert("RGB")
    print(f"Selfie size: {selfie.size}")

    # Get phone cutout
    if args.skip_rembg:
        phone_cutout = Image.open(args.phone).convert("RGBA")
    else:
        api_key = os.environ.get("FAL_KEY")
        if not api_key:
            print("Error: FAL_KEY required for background removal.", file=sys.stderr)
            sys.exit(1)
        phone_cutout = remove_background(args.phone)

    print(f"Phone cutout size: {phone_cutout.size}")
    print(f"Target bbox: {bbox}")

    # Composite
    result = composite_phone(
        selfie, phone_cutout, bbox,
        rotation=args.rotation,
        feather=args.feather,
        brightness=args.brightness,
    )

    # Save
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(str(output_path), "PNG", quality=95)

    full_path = output_path.resolve()
    print(f"\nImage saved: {full_path}")
    if not args.no_media_tag:
        print(f"MEDIA:{full_path}")


if __name__ == "__main__":
    main()
