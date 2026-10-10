#!/usr/bin/env python3
"""Projector alignment test pattern: get both projectors to the same scale and
alignment on the house.

Run it on each Pi with ./test_pattern.sh (stops the eye service, shows the
pattern, starts the service again when you quit). See docs/OPERATIONS.md
"Lining up the two projectors".

Two layers:

  Frame (fixed to the projector's picture) -- adjust the PROJECTORS with this:
    * border + corner brackets  -> a rectangle on the wall, not a trapezoid (keystone)
    * corner circles            -> all four the same size and round
    * horizon ruler, % of width -> measure "10%" to "10%" on each projector and
                                   zoom until both give the same distance
    * horizon line              -> level, and at the same height on both
    * grid                      -> 1/9 of the height per square

  Eye (from this Pi's config.ini [display] center_x / center_y / scale)
    * thick circle = the eye's radius (0.42 x height x scale, as the renderer)
    * dashed shape = the walker eye's lid opening at rest, inner circle = iris
    Fine-tune with config.ini instead of moving a projector: the pattern
    re-reads config.ini when it changes, so edit it in another SSH window and
    watch the outline move.

Usage:
  ./test_pattern.sh                       # on a Pi (recommended)
  python3 test_pattern.py                 # display must be free
  python3 test_pattern.py --color red --seconds 600
  python3 test_pattern.py --png pattern.png   # save an image, no display

Keys (if a keyboard is plugged in): C colour, G grid, E eye outline,
I invert, Q / Esc quit. Ctrl+C in the SSH window also quits.
"""

import argparse
import math
import os
import socket
import sys
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

import settings as S  # noqa: E402

COLOURS = {
    "white": (255, 255, 255),
    "red": (255, 30, 30),
    "green": (40, 255, 40),
    "yellow": (255, 220, 0),
}

# Must match renderers.py (uEyeR = H * 0.42 * scale) and the walker shader's
# resting lids (eye_shaders.WALKER_FRAG: lidW, archTop, archBot, irisR).
EYE_R_FRAC = 0.42
WALKER_LID_W = 1.15
WALKER_TOP = (0.58, 0.50)      # (exponent, height) of the upper lid arch
WALKER_BOT = (0.82, 0.46)      # (exponent, depth) of the lower lid arch
WALKER_IRIS_R = 0.58


def default_label():
    host = socket.gethostname().lower()
    if "left" in host:
        return "LEFT"
    if "right" in host:
        return "RIGHT"
    return host.split(".")[0].upper()


def output_size(mode):
    """Same rule as demoneye.output_size: "1280x720" -> (1280, 720); anything
    else (native/auto) -> (0, 0) = the projector's preferred mode."""
    try:
        w, h = (int(v) for v in str(mode).lower().replace(" ", "").split("x"))
        if w > 0 and h > 0:
            return (w, h)
    except ValueError:
        pass
    return (0, 0)


def eye_geometry(w, h):
    """Eye centre (pixels, y down) and radius from the loaded settings."""
    return (S.EYE_CENTER_X * w, S.EYE_CENTER_Y * h, h * EYE_R_FRAC * S.EYE_SCALE)


def walker_lid_points(cx, cy, r, n=120):
    """Outline of the walker eye's resting lid opening, in screen pixels."""
    top, bot = [], []
    for i in range(n + 1):
        x = -WALKER_LID_W + 2 * WALKER_LID_W * i / n
        base = max(0.0, 1.0 - (x / WALKER_LID_W) ** 2)
        top.append((cx + x * r, cy - base ** WALKER_TOP[0] * WALKER_TOP[1] * r))
        bot.append((cx + x * r, cy + base ** WALKER_BOT[0] * WALKER_BOT[1] * r))
    return top + bot[::-1]


def dashed(surface, colour, pts, width, dash=14):
    """Closed dashed polyline."""
    on, run = True, 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        seg = math.hypot(x1 - x0, y1 - y0)
        if on:
            pygame.draw.line(surface, colour, (x0, y0), (x1, y1), width)
        run += seg
        if run >= dash:
            on, run = not on, 0.0


def draw(surface, label, colour, show_grid=True, invert=False, eye=None):
    """Draw the pattern. eye = (cx, cy, r) in pixels, or None to hide it."""
    w, h = surface.get_size()
    cx, cy = w // 2, h // 2
    bg, fg = ((0, 0, 0), colour) if not invert else (colour, (0, 0, 0))
    dim = tuple(c // 3 for c in fg) if not invert else (90, 90, 90)
    surface.fill(bg)
    step = max(10, h // 9)

    if show_grid:
        x = cx % step
        while x < w:
            pygame.draw.line(surface, dim, (x, 0), (x, h), 1)
            x += step
        y = cy % step
        while y < h:
            pygame.draw.line(surface, dim, (0, y), (w, y), 1)
            y += step

    # border + corner brackets (keystone)
    pygame.draw.rect(surface, fg, (0, 0, w, h), 4)
    arm = h // 8
    for (x0, y0, dx, dy) in ((0, 0, 1, 1), (w - 1, 0, -1, 1),
                             (0, h - 1, 1, -1), (w - 1, h - 1, -1, -1)):
        pygame.draw.line(surface, fg, (x0, y0 + dy * 12), (x0 + dx * arm, y0 + dy * 12), 6)
        pygame.draw.line(surface, fg, (x0 + dx * 12, y0), (x0 + dx * 12, y0 + dy * arm), 6)

    # horizon + vertical centre lines
    pygame.draw.line(surface, fg, (0, cy), (w, cy), 3)
    pygame.draw.line(surface, fg, (cx, 0), (cx, h), 3)

    # ruler ticks in % of width: small every 1%, medium every 5%, big every 10%
    font_s = pygame.font.Font(None, max(18, h // 32))

    def tick_len(i):
        return h // 24 if i % 10 == 0 else (h // 60 if i % 5 == 0 else h // 120)

    for i in range(-50, 51):
        x = cx + round(i * w / 100)
        if 0 <= x < w:
            big = i % 10 == 0
            half = tick_len(i)
            pygame.draw.line(surface, fg, (x, cy - half), (x, cy + half), 3 if big else 1)
            if big and 0 < abs(i) < 50:
                t = font_s.render(f"{abs(i)}%", True, fg)
                surface.blit(t, (x - t.get_width() // 2, cy + half + 4))
        y = cy + round(i * w / 100)          # same spacing on the vertical line
        if 0 <= y < h and i != 0:
            half = tick_len(i)
            pygame.draw.line(surface, fg, (cx - half, y), (cx + half, y),
                             3 if i % 10 == 0 else 1)

    # roundness circles about the picture centre
    for frac in (0.15, 0.3):
        pygame.draw.circle(surface, fg, (cx, cy), round(h * frac), 2)
    pygame.draw.circle(surface, fg, (cx, cy), 6)

    # corner circles: identical size and round on the wall
    r = h // 14
    for (x, y) in ((r + 30, r + 30), (w - r - 30, r + 30),
                   (r + 30, h - r - 30), (w - r - 30, h - r - 30)):
        pygame.draw.circle(surface, fg, (x, y), r, 3)
        pygame.draw.line(surface, fg, (x - r, y), (x + r, y), 1)
        pygame.draw.line(surface, fg, (x, y - r), (x, y + r), 1)

    # the eye, where config.ini puts it
    if eye:
        ex, ey, er = eye
        pygame.draw.circle(surface, fg, (round(ex), round(ey)), round(er), 5)
        dashed(surface, fg, walker_lid_points(ex, ey, er), 3)
        pygame.draw.circle(surface, fg, (round(ex), round(ey)), round(er * WALKER_IRIS_R), 1)
        arm_e = max(10, round(er * 0.12))
        pygame.draw.line(surface, fg, (ex - arm_e, ey - arm_e), (ex + arm_e, ey + arm_e), 3)
        pygame.draw.line(surface, fg, (ex - arm_e, ey + arm_e), (ex + arm_e, ey - arm_e), 3)

    # labels, on a background box so lines don't run through them
    font_l = pygame.font.Font(None, h // 6)
    t = font_l.render(label, True, fg)
    lx, ly = round(w * 0.16) - t.get_width() // 2, h // 5
    pygame.draw.rect(surface, bg, (lx - 8, ly - 4, t.get_width() + 16, t.get_height() + 8))
    surface.blit(t, (lx, ly))
    info = f"{w}x{h}   grid {step}px   ticks = % of width"
    if eye:
        info += (f"   eye x={S.EYE_CENTER_X:.2f} y={S.EYE_CENTER_Y:.2f}"
                 f" scale={S.EYE_SCALE:.2f}")
    it = font_s.render(info, True, fg)
    ix, iy = r * 2 + 50, h - it.get_height() - 16
    pygame.draw.rect(surface, bg, (ix - 4, iy - 2, it.get_width() + 8, it.get_height() + 4))
    surface.blit(it, (ix, iy))


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(here, "config.ini"),
                    help="config.ini to take output_mode and the eye position from")
    ap.add_argument("--label", default=None,
                    help="text to show (default: LEFT/RIGHT from the hostname)")
    ap.add_argument("--color", default="white", choices=COLOURS)
    ap.add_argument("--output-mode", metavar="WxH",
                    help="override config output_mode (default: config, 1280x720)")
    ap.add_argument("--no-grid", action="store_true")
    ap.add_argument("--no-eye", action="store_true", help="hide the eye outline")
    ap.add_argument("--invert", action="store_true", help="dark lines on a lit background")
    ap.add_argument("--seconds", type=float, default=0,
                    help="quit after N seconds (0 = never)")
    ap.add_argument("--png", metavar="FILE", help="save the pattern to a PNG and exit")
    args = ap.parse_args(argv)

    S.load_config(args.config)
    mode = args.output_mode or S.OUTPUT_MODE
    label = args.label or default_label()
    names = list(COLOURS)
    ci = names.index(args.color)
    grid, invert, show_eye = not args.no_grid, args.invert, not args.no_eye

    pygame.display.init()      # display only: no audio (see demoneye._open_window)
    pygame.font.init()
    try:
        if args.png:
            size = output_size(mode)
            surf = pygame.Surface(size if size != (0, 0) else (1280, 720))
            eye = eye_geometry(*surf.get_size()) if show_eye else None
            draw(surf, label, COLOURS[names[ci]], grid, invert, eye)
            pygame.image.save(surf, args.png)
            print(f"saved {args.png}")
            return 0

        if S.SDL_DRIVER:
            os.environ["SDL_VIDEODRIVER"] = S.SDL_DRIVER
        try:
            screen = pygame.display.set_mode(output_size(mode), pygame.FULLSCREEN)
        except pygame.error as e:
            print(f"[fatal] could not open the display: {e}\n"
                  "        Is the eye service still running? Use ./test_pattern.sh, or\n"
                  "        sudo systemctl stop demoneye first. Over SSH set\n"
                  "        SDL_VIDEODRIVER=kmsdrm SDL_KMSDRM_DEVICE_INDEX=1.", file=sys.stderr)
            return 1
        pygame.mouse.set_visible(False)
        w, h = screen.get_size()
        clock = pygame.time.Clock()
        end = time.monotonic() + args.seconds if args.seconds > 0 else None
        cfg_mtime, next_check = _mtime(args.config), 0.0
        dirty = True
        print(f"Test pattern '{label}' at {w}x{h}. Ctrl+C (or Q) to quit.", flush=True)
        while True:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    return 0
                if ev.type == pygame.KEYDOWN:
                    if ev.key in (pygame.K_q, pygame.K_ESCAPE):
                        return 0
                    if ev.key == pygame.K_c:
                        ci = (ci + 1) % len(names)
                    elif ev.key == pygame.K_g:
                        grid = not grid
                    elif ev.key == pygame.K_e:
                        show_eye = not show_eye
                    elif ev.key == pygame.K_i:
                        invert = not invert
                    dirty = True
            now = time.monotonic()
            if now >= next_check:                 # follow config.ini edits live
                next_check = now + 0.5
                m = _mtime(args.config)
                if m != cfg_mtime:
                    cfg_mtime = m
                    try:
                        S.load_config(args.config)
                        print(f"[pattern] eye x={S.EYE_CENTER_X:.2f} y={S.EYE_CENTER_Y:.2f}"
                              f" scale={S.EYE_SCALE:.2f}", flush=True)
                        dirty = True
                    except Exception as e:      # half-saved file: keep the old values
                        print(f"[pattern] config.ini not readable yet: {e}", flush=True)
            if dirty:
                eye = eye_geometry(w, h) if show_eye else None
                draw(screen, label, COLOURS[names[ci]], grid, invert, eye)
                pygame.display.flip()
                dirty = False
            if end and now > end:
                return 0
            clock.tick(20)
    except KeyboardInterrupt:
        return 0
    finally:
        pygame.quit()


if __name__ == "__main__":
    sys.exit(main())
