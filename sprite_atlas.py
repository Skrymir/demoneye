#!/usr/bin/env python3
"""
Pack a PNG frame sequence into a sprite atlas for stage mode.

Use this on a walk cycle rendered out of Blender (or anywhere): render the clip
to a numbered PNG sequence with a transparent background, then

    python sprite_atlas.py frames/ figures/shambler

writes figures/shambler.png (a grid atlas) and figures/shambler.json (metadata).
Point `figure_atlas` in config.ini [stage] at the .png and demoneye samples the
right cell from the streamed walk phase.

Blender tips for a silhouette:
  * orthographic camera, straight on, character walking in place
  * world background transparent (Render Properties > Film > Transparent)
  * a pure black emission material, or just use the alpha and ignore colour
  * square-ish output (e.g. 256x256) keeps the atlas tidy
  * export the whole cycle; 24-32 frames loops smoothly
"""

import json
import math
import os
import sys

from PIL import Image


def pack(frame_paths, out_base, cell=None, columns=None, trim=True):
    imgs = [Image.open(p).convert("RGBA") for p in frame_paths]
    if not imgs:
        raise SystemExit("no frames found")

    if trim:
        # Crop every frame to the union of their non-transparent pixels, so the
        # figure fills the cell and the feet line up across frames.
        boxes = [im.getbbox() for im in imgs]
        boxes = [b for b in boxes if b]
        if boxes:
            l = min(b[0] for b in boxes); t = min(b[1] for b in boxes)
            r = max(b[2] for b in boxes); b_ = max(b[3] for b in boxes)
            imgs = [im.crop((l, t, r, b_)) for im in imgs]

    w, h = imgs[0].size
    if cell:
        imgs = [im.resize((cell, cell), Image.LANCZOS) for im in imgs]
        w = h = cell

    n = len(imgs)
    cols = columns or int(math.ceil(math.sqrt(n)))
    rows = int(math.ceil(n / cols))
    atlas = Image.new("RGBA", (cols * w, rows * h), (0, 0, 0, 0))
    for i, im in enumerate(imgs):
        atlas.paste(im, ((i % cols) * w, (i // cols) * h))

    png = out_base + ".png"
    meta = out_base + ".json"
    os.makedirs(os.path.dirname(png) or ".", exist_ok=True)
    atlas.save(png)
    with open(meta, "w") as f:
        json.dump({"frames": n, "cols": cols, "rows": rows,
                   "cell_w": w, "cell_h": h}, f, indent=2)
    print(f"[atlas] {n} frames -> {cols}x{rows} grid, cell {w}x{h}")
    print(f"[atlas] wrote {png} and {meta}")
    return png, meta


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    src, out_base = sys.argv[1], sys.argv[2]
    cell = int(sys.argv[3]) if len(sys.argv) > 3 else None
    if os.path.isdir(src):
        names = sorted(n for n in os.listdir(src)
                       if n.lower().endswith((".png", ".tga", ".tif", ".tiff")))
        paths = [os.path.join(src, n) for n in names]
    else:
        paths = [src]
    pack(paths, out_base, cell=cell)


if __name__ == "__main__":
    main()
