"""
Shared GLSL: one prelude of helpers every shader uses, and the assembler that
turns a shader body into a complete source for the target GL profile.

Shader bodies in eye_shaders.py contain NO helper definitions and NO
#version line — assemble() supplies both, plus any #defines.
"""

VERT = """
in vec2 in_pos;
void main(){ gl_Position = vec4(in_pos, 0.0, 1.0); }
"""

# Every helper any shader uses. Names are chosen to avoid the reserved
# built-ins of desktop GLSL (noise1..4 are reserved, hence vnoise).
PRELUDE = """
// ---- hashing / noise ----
float hash21(vec2 p){ p=fract(p*vec2(123.34,345.45)); p+=dot(p,p+34.345); return fract(p.x*p.y); }
float vnoise(vec2 p){
    vec2 i=floor(p), f=fract(p); f=f*f*(3.0-2.0*f);
    float a=hash21(i), b=hash21(i+vec2(1,0)), c=hash21(i+vec2(0,1)), d=hash21(i+vec2(1,1));
    return mix(mix(a,b,f.x), mix(c,d,f.x), f.y);
}
float fbm(vec2 p){ float s=0.0,a=0.5; for(int i=0;i<5;i++){ s+=a*vnoise(p); p*=2.02; a*=0.5; } return s; }
float fbm4(vec2 p){ float s=0.0,a=0.5; for(int i=0;i<4;i++){ s+=a*vnoise(p); p*=2.02; a*=0.5; } return s; }
float voro(vec2 p){
    vec2 i=floor(p), f=fract(p); float md=8.0;
    for(int y=-1;y<=1;y++) for(int x=-1;x<=1;x++){
        vec2 g=vec2(float(x),float(y));
        vec2 o=vec2(hash21(i+g), hash21(i+g+19.7));
        vec2 r=g+o-f; md=min(md, dot(r,r));
    }
    return sqrt(md);
}
"""


def assemble(body, stage, gles, defines=None):
    """Return complete shader source: version header, defines, prelude, body."""
    head = "#version 300 es\n" if gles else "#version 330\n"
    if gles and stage == "frag":
        head += "precision highp float;\n"
    if defines:
        head += "".join(f"#define {k} {v}\n" for k, v in defines.items())
    if stage == "frag":
        return head + PRELUDE + body
    return head + body
