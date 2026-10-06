"""Small OpenGL helpers: compile/link, the fullscreen triangle, texture upload."""

import json
import os

from OpenGL import GL

from glsl_common import VERT, assemble


def _compile(src, stage_enum, label):
    sh = GL.glCreateShader(stage_enum)
    GL.glShaderSource(sh, src)
    GL.glCompileShader(sh)
    if not GL.glGetShaderiv(sh, GL.GL_COMPILE_STATUS):
        log = GL.glGetShaderInfoLog(sh).decode(errors="replace")
        raise RuntimeError(f"{label} shader failed to compile:\n{log}")
    return sh


def build_program(frag_body, gles, defines=None):
    """Compile the shared vertex shader + a fragment body into a linked program."""
    vs = _compile(assemble(VERT, "vert", gles), GL.GL_VERTEX_SHADER, "vertex")
    fs = _compile(assemble(frag_body, "frag", gles, defines), GL.GL_FRAGMENT_SHADER, "fragment")
    prog = GL.glCreateProgram()
    GL.glAttachShader(prog, vs)
    GL.glAttachShader(prog, fs)
    GL.glBindAttribLocation(prog, 0, "in_pos")
    GL.glLinkProgram(prog)
    if not GL.glGetProgramiv(prog, GL.GL_LINK_STATUS):
        raise RuntimeError("program link failed:\n" + GL.glGetProgramInfoLog(prog).decode())
    GL.glDeleteShader(vs)
    GL.glDeleteShader(fs)
    return prog


def make_fullscreen_triangle():
    """One triangle that covers the screen; the fragment shader does the rest."""
    import ctypes
    verts = (GL.GLfloat * 6)(-1, -1, 3, -1, -1, 3)
    vao = GL.glGenVertexArrays(1)
    vbo = GL.glGenBuffers(1)
    GL.glBindVertexArray(vao)
    GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
    GL.glBufferData(GL.GL_ARRAY_BUFFER, ctypes.sizeof(verts), verts, GL.GL_STATIC_DRAW)
    GL.glEnableVertexAttribArray(0)
    GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, GL.GL_FALSE, 0, ctypes.c_void_p(0))
    GL.glBindVertexArray(0)
    return vao


def load_atlas(png_path):
    """Upload a sprite atlas PNG (+ its .json) as a GL texture. (tex, meta) or None."""
    try:
        from PIL import Image
    except ImportError:
        print("[stage] Pillow not installed — cannot load a sprite atlas", flush=True)
        return None
    meta_path = os.path.splitext(png_path)[0] + ".json"
    try:
        with open(meta_path) as f:
            meta = json.load(f)
        img = Image.open(png_path).convert("RGBA")
    except Exception as e:
        print(f"[stage] could not load atlas {png_path}: {e}", flush=True)
        return None
    tex = GL.glGenTextures(1)
    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    for pname, val in ((GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR), (GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR),
                       (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
        GL.glTexParameteri(GL.GL_TEXTURE_2D, pname, val)
    GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, img.width, img.height, 0,
                    GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, img.tobytes())
    print(f"[stage] atlas {os.path.basename(png_path)}: {meta['frames']} frames "
          f"{meta['cols']}x{meta['rows']} cell {meta['cell_w']}x{meta['cell_h']}", flush=True)
    return tex, meta
