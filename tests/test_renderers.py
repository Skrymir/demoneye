"""Every eye builds its program and draws a frame (offscreen GLES).
Skipped where no GL context is available."""
import pytest

import settings as S
from renderers import make_renderer, EYE_FIGURES


@pytest.fixture(scope="module")
def gl():
    import sys
    if not sys.platform.startswith("linux"):
        pytest.skip("headless GL (EGL) is Linux-only; shader compilation is covered by test_shaders")
    import pygame
    from OpenGL import GL
    S.load_config("config.ini")
    pygame.init()
    try:
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_PROFILE_MASK, pygame.GL_CONTEXT_PROFILE_ES)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MAJOR_VERSION, 3)
        pygame.display.gl_set_attribute(pygame.GL_CONTEXT_MINOR_VERSION, 0)
        pygame.display.set_mode((320, 180), pygame.OPENGL | pygame.DOUBLEBUF)
    except pygame.error as e:
        pytest.skip(f"no GL context: {e}")
    yield GL
    pygame.quit()


@pytest.mark.parametrize("eye", EYE_FIGURES)
def test_renderer_builds_and_draws(gl, eye):
    import demoneye as D
    from glutil import build_program, make_fullscreen_triangle
    r = make_renderer(eye)
    app = D.EyeApp.__new__(D.EyeApp)
    app.active, app.stale, app.renderer, app.schema = True, False, r, r.schema
    app.prog = build_program(r.frag, True, r.defines)
    app.vao = make_fullscreen_triangle()
    gl.glUseProgram(app.prog); r.bind(app.prog)
    app.W, app.H, app.t = 320, 180, 1.0
    app.disp = dict(r.schema)
    gl.glViewport(0, 0, 320, 180)
    app._draw()
    assert gl.glGetError() == gl.GL_NO_ERROR


def test_unknown_eye_is_a_clear_error():
    with pytest.raises(ValueError, match="walker"):
        make_renderer("dragon")
    with pytest.raises(ValueError):
        make_renderer("ghost")              # stage figures are gone


def test_reduced_resolution_render_target(gl):
    """render_height: draw offscreen at a lower height, blit up to the screen."""
    import demoneye as D
    from glutil import build_program, make_fullscreen_triangle
    saved = S.RENDER_HEIGHT
    try:
        S.RENDER_HEIGHT = 90
        r = make_renderer("demon")
        app = D.EyeApp.__new__(D.EyeApp)
        app.active, app.stale, app.renderer, app.schema = True, False, r, r.schema
        app.prog = build_program(r.frag, True, r.defines)
        app.vao = make_fullscreen_triangle()
        gl.glUseProgram(app.prog); r.bind(app.prog)
        app.W, app.H, app.t = 320, 180, 1.0
        app.out_W, app.out_H = 320, 180
        app.disp = dict(r.schema)
        app._setup_render_target()
        assert app.fbo and (app.W, app.H) == (160, 90)
        app._draw()
        assert gl.glGetError() == gl.GL_NO_ERROR
        S.RENDER_HEIGHT = 0                       # native: no offscreen target
        app.W, app.H = 320, 180
        app._setup_render_target()
        assert app.fbo is None and (app.W, app.H) == (320, 180)
    finally:
        S.RENDER_HEIGHT = saved
