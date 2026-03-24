#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pillow>=10.0.0",
#     "numpy>=1.26.0",
# ]
# ///
"""
Detect the phone region in a selfie and composite a reference phone image onto it.
Uses color-based detection to find the white/light phone region.

Usage:
  uv run phone_swap.py --selfie selfie.jpg --phone phone.jpg --output result.png
"""

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def find_phone_region(img_array: np.ndarray) -> tuple[int, int, int, int] | None:
    """
    Find the phone region in the selfie by looking for a bright rectangular 
    object near the center-right area (typical selfie phone position).
    Returns (x1, y1, x2, y2) or None.
    """
    h, w = img_array.shape[:2]
    
    # The phone is typically held in the center area of a mirror selfie
    # Look for bright/white rectangular regions
    # Convert to grayscale for brightness detection
    gray = np.mean(img_array[:, :, :3], axis=2)
    
    # Phone is likely white/bright - threshold for bright pixels
    bright_mask = gray > 200  # white/near-white
    
    # Focus on the area where a phone would be held (middle section)
    # In a mirror selfie, the phone is roughly in the center-upper area
    search_y_start = int(h * 0.15)
    search_y_end = int(h * 0.55)
    search_x_start = int(w * 0.25)
    search_x_end = int(w * 0.75)
    
    sub_mask = bright_mask[search_y_start:search_y_end, search_x_start:search_x_end]
    
    # Find bounding box of bright pixels in search region
    rows = np.any(sub_mask, axis=1)
    cols = np.any(sub_mask, axis=0)
    
    if not rows.any() or not cols.any():
        return None
    
    rmin, rmax = np.where(rows)[0][[0, -1]]
    cmin, cmax = np.where(cols)[0][[0, -1]]
    
    # Convert back to full image coordinates
    x1 = search_x_start + int(cmin)
    y1 = search_y_start + int(rmin)
    x2 = search_x_start + int(cmax)
    y2 = search_y_start + int(rmax)
    
    # Sanity check - phone should be roughly portrait-shaped
    phone_w = x2 - x1
    phone_h = y2 - y1
    aspect = phone_h / max(phone_w, 1)
    
    if aspect < 1.2 or aspect > 3.5:
        # Try to refine - maybe too much noise
        # Shrink to core bright area
        pass
    
    return (x1, y1, x2, y2)


def main():
    parser = argparse.ArgumentParser(description="Swap phone in selfie with reference phone image")
    parser.add_argument("--selfie", required=True, help="Path to the selfie image")
    parser.add_argument("--phone", required=True, help="Path to the reference phone image")
    parser.add_argument("--output", "-o", required=True, help="Output path")
    parser.add_argument("--phone-bbox", default=None, help="Manual phone bounding box: x1,y1,x2,y2")
    
    args = parser.parse_args()
    
    selfie = Image.open(args.selfie).convert("RGB")
    phone_ref = Image.open(args.phone).convert("RGBA")
    selfie_array = np.array(selfie)
    
    if args.phone_bbox:
        coords = [int(x) for x in args.phone_bbox.split(",")]
        bbox = tuple(coords)
    else:
        bbox = find_phone_region(selfie_array)
    
    if bbox is None:
        print("Error: Could not detect phone region. Use --phone-bbox x1,y1,x2,y2", file=sys.stderr)
        sys.exit(1)
    
    x1, y1, x2, y2 = bbox
    phone_w = x2 - x1
    phone_h = y2 - y1
    
    print(f"Phone region detected: ({x1},{y1}) → ({x2},{y2}), size {phone_w}x{phone_h}")
    
    # Resize reference phone to fit the detected region
    phone_resized = phone_ref.resize((phone_w, phone_h), Image.LANCZOS)
    
    # Composite onto selfie
    result = selfie.copy()
    # If phone_ref has alpha channel, use it as mask
    if phone_resized.mode == "RGBA":
        result.paste(phone_resized, (x1, y1), phone_resized)
    else:
        result.paste(phone_resized, (x1, y1))
    
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(str(output_path), "PNG")
    print(f"Image saved: {output_path.resolve()}")


if __name__ == "__main__":
    main()
