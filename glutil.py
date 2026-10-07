"""Small OpenGL helpers: compile/link and the fullscreen triangle."""

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
