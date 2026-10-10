"""Fragment shader BODIES for the three eyes. No helpers or #version here —
glsl_common.assemble() prepends the shared prelude. Uniforms: iResolution,
iTime, uGaze, uBlink, uDilation, uGlow, uCenter, uEyeR, uMirror, uOpen,
uSlant, uGazeScale.
"""

DEMON_FRAG = """\
uniform vec2  iResolution;
uniform float iTime;
uniform vec2  uGaze;
uniform float uBlink;
uniform float uDilation;
uniform float uGlow;
uniform vec2  uCenter;
uniform float uEyeR;
uniform float uMirror;
uniform float uOpen;      // lid aperture: ~0.6 angry squint, 1.0 neutral, ~1.35 surprise
uniform float uSlant;     // upper-lid tilt: 0 neutral, + = angry glare
uniform vec2  uGazeScale; // multiplies gaze travel (from config)
out vec4 fragColor;
// F1 voronoi distance — cellular iris texture
void main(){
    vec2 center = uCenter*iResolution;
    vec2 q = (gl_FragCoord.xy - center)/uEyeR;   // eye space, y up
    float xs = (uMirror>0.5)? -1.0 : 1.0;
    float blink = clamp(uBlink,0.0,1.0);
    float openU = clamp(uOpen, 0.0, 1.6);
    float sl = clamp(uSlant,-1.0,1.0) * xs;

    // fierce base tilt of the whole eye (mirrored so a pair is symmetric)
    float ta = 0.08*xs;
    float ca = cos(ta), sa = sin(ta);
    vec2 qe = vec2(ca*q.x + sa*q.y, -sa*q.x + ca*q.y);

    // ---- hooded, asymmetric reptilian aperture ----
    float open_e = openU * (1.0 - blink);
    float lidW = 1.45;
    float nx = clamp(qe.x/lidW, -1.0, 1.0);
    // one corner sharper than the other; upper lid flatter (hooded)
    float kc = mix(0.72, 0.40, (nx*xs+1.0)*0.5);
    float baseT = pow(max(0.0, 1.0-nx*nx), kc*0.80);
    float baseB = pow(max(0.0, 1.0-nx*nx), kc);
    float archTop =  baseT*open_e*(0.68 - 0.10*max(sl,0.0)) - sl*0.24*baseT*nx*open_e;
    float archBot = -baseB*open_e*(0.58 - 0.08*max(sl,0.0)) + baseB*open_e*0.04;  // lower lid bulges up
    float lid = smoothstep(0.0,0.026, archTop-qe.y)
              * smoothstep(0.0,0.026, qe.y-archBot)
              * smoothstep(0.0,0.02, lidW-abs(qe.x));
    float outside = max(max(qe.y-archTop, archBot-qe.y), abs(qe.x)-lidW);
    float edgeIn  = min(archTop-qe.y, qe.y-archBot);          // depth inside aperture

    // ---- the iris fills the WHOLE aperture (no sclera) ----
    // Only computed inside the lids: it's multiplied by lid (0 outside) below,
    // and it's the expensive part (2x voronoi + fbm). Skipping it everywhere
    // else gives the identical picture at about half the GPU cost.
    vec3 iris = vec3(0.0);
    if(lid > 0.0){
        vec2 gaze = uGaze*uGazeScale*vec2(0.34,0.24);
        vec2 pI = qe - gaze;                     // pupil-centred coords
        float rr = length(pI);
        float ang = atan(pI.y, pI.x);

        // cellular + fibrous texture
        float cellD = voro(pI*13.0 + 3.0);
        float cell  = smoothstep(0.80,0.25, cellD);            // fine cell bodies, soft seams
        float cell2 = smoothstep(0.85,0.30, voro(pI*23.0 + 11.0));
        float fib   = fbm(vec2(ang*9.0, rr*2.6));

        vec3 hotC   = vec3(1.00,0.74,0.22);
        vec3 midC   = vec3(0.96,0.34,0.05);
        vec3 deepC  = vec3(0.28,0.03,0.01);
        iris = mix(hotC, midC, smoothstep(0.08,0.55, rr));
        iris = mix(iris, deepC, smoothstep(0.55,1.30, rr));
        iris *= 0.72 + 0.28*cell;
        iris *= 0.86 + 0.14*cell2;
        iris *= 0.74 + 0.44*fib;
        iris *= 1.0 + 0.55*exp(-rr*rr*9.0);                    // furnace glow around the pupil
        float aoT = smoothstep(0.0,0.34, archTop-qe.y);        // hooded upper-lid shadow
        float aoB = smoothstep(0.0,0.16, qe.y-archBot);
        iris *= (0.52 + 0.48*aoT) * (0.72 + 0.28*aoB);
        iris *= uGlow;

        // ---- ragged organic slit pupil (widens + rounds with dilation) ----
        float d = clamp(uDilation,0.0,1.0);
        float Hp = 0.56;
        float open_amt = smoothstep(0.62,1.0, d);
        float Wp = Hp*(0.06+0.17*d);
        Wp = Wp + (Hp-Wp)*open_amt;
        float k = 0.60 + (0.5-0.60)*open_amt;
        float yn = pI.y/Hp;
        float rag = 1.0 - open_amt;                            // raggedness fades as it rounds
        float px = pI.x - yn*0.07*xs*rag;                      // slight diagonal shear
        float hwB = Wp*pow(max(0.0,1.0-yn*yn), k);
        float wobL = 1.0 + 0.48*(vnoise(vec2(yn*4.2, 7.0))-0.5)*rag;
        float wobR = 1.0 + 0.48*(vnoise(vec2(yn*4.2,17.0))-0.5)*rag;
        float e = 0.010;
        float inY = step(abs(yn), 1.0);
        float pupil = inY * smoothstep(e,-e, max(-px-hwB*wobL, px-hwB*wobR));
        float rim   = inY * smoothstep(e,-e, max(-px-(hwB*wobL+0.045), px-(hwB*wobR+0.045)));
        iris = mix(iris, vec3(1.0,0.42,0.10)*uGlow, clamp(rim-pupil,0.0,1.0)*0.85);
        iris = mix(iris, vec3(0.0), pupil);

        // ---- wet cornea: fixed window-style reflections (don't follow the gaze) ----
        vec2 h1 = vec2(-0.16*xs, 0.14);
        vec2 h2 = vec2(-0.06*xs,-0.06);
        float s1 = smoothstep(0.085,0.015, length(max(abs(qe-h1)-vec2(0.050,0.034),0.0)));
        float s2 = smoothstep(0.060,0.010, length(max(abs(qe-h2)-vec2(0.028,0.020),0.0)))*0.55;
        iris += (s1+s2)*vec3(1.0,0.97,0.9)*clamp(open_e,0.0,1.0);

        // soft inner glow so the whole aperture burns
        iris += vec3(1.0,0.15,0.05)*exp(-rr*rr*2.0)*0.18*uGlow;
    }

    // ---- compose: eye inside lids, spill + lit hide outside ----
    float lit = uGlow * (0.22 + 0.78*clamp(open_e,0.0,1.0));
    vec3 halo = vec3(1.0,0.12,0.05) * exp(-pow(length(q)*1.25, 2.0)) * lit;
    vec3 col = iris*lid + halo*0.30*(1.0-lid);

    if(outside > -0.06 && outside < 0.60){
        // dark crease right where lid meets eye, then a lit scaled ridge
        float crease = smoothstep(0.030, 0.0, abs(outside));
        float fall = smoothstep(0.45, 0.03, max(outside,0.0));
        float sc = fbm(qe*vec2(6.0,7.0)+7.0);
        float ridgeT = 1.0 - abs(sc*2.0-1.0);
        float tex = 0.24 + 0.76*pow(ridgeT,2.2);
        vec3 skin = vec3(0.42,0.08,0.02) * tex * fall * lit;
        float rimBand = smoothstep(0.02,0.05, outside)*smoothstep(0.14,0.06, outside);
        float below = smoothstep(0.05,-0.10, qe.y-(archBot+archTop)*0.5);
        skin += vec3(0.95,0.30,0.07) * rimBand * mix(0.18, 0.75, below) * lit;
        float browBand  = smoothstep(0.02,0.16, qe.y-archTop) * smoothstep(0.55,0.18, qe.y-archTop);
        float lowerBand = smoothstep(0.02,0.10, archBot-qe.y) * smoothstep(0.40,0.12, archBot-qe.y);
        skin *= 1.0 + (0.45 + 0.7*max(sl*xs,0.0))*browBand;   // heavy hot brow
        skin *= 1.0 + 0.9*lowerBand;                           // lower lid catches the fire
        col += skin*(1.0-lid)*(1.0-0.7*crease);
        col *= 1.0 - 0.35*crease;                              // recessed seam
    }

    fragColor = vec4(col, 1.0);
}
"""

HUMAN_FRAG = """\
uniform vec2  iResolution;
uniform float iTime;
uniform vec2  uGaze;       // -1..1 look direction
uniform float uBlink;      // 0 open .. 1 shut
uniform float uDilation;   // 0..1 pupil size
uniform vec2  uCenter;     // eyeball center, fraction of screen (y up)
uniform float uEyeR;       // eyeball radius in pixels
uniform float uMirror;     // 0/1 flip horizontally
uniform float uOpen;      // lid aperture: squint..wide
uniform float uSlant;     // upper-lid tilt (anger)
uniform vec2  uGazeScale; // multiplies gaze travel (from config)
out vec4 fragColor;
void main(){
    vec2 center = uCenter*iResolution;
    vec2 q = (gl_FragCoord.xy - center)/uEyeR;   // eye space, y up, radius ~1
    float xs = (uMirror>0.5)? -1.0 : 1.0;

    // ---- eyelids: emotion-aware aperture (uOpen/uSlant) + blink ----
    float openU = clamp(uOpen, 0.0, 1.6);
    float slM = clamp(uSlant, -1.0, 1.0);
    float sl = slM * xs;
    float angry = max(slM, 0.0);
    float open_e = openU * (1.0 - uBlink);
    float lidW = 1.58;
    float nx = clamp(q.x/lidW, -1.0, 1.0);
    float baseA = pow(max(0.0,1.0-nx*nx), 0.62);
    float baseL = pow(max(0.0,1.0-nx*nx), 0.85);
    float archTop =  baseA*open_e*(0.64 - 0.08*angry) - sl*0.20*baseA*nx*open_e;
    float archBot = -baseL*open_e*(0.56 - 0.06*angry);
    float lid = smoothstep(0.0,0.025, archTop-q.y)
              * smoothstep(0.0,0.025, q.y-archBot)
              * smoothstep(0.0,0.02, lidW-abs(q.x));
    float outside = max(max(q.y-archTop, archBot-q.y), abs(q.x)-lidW);
    if(outside > 0.62){ fragColor=vec4(0.0,0.0,0.0,1.0); return; }

    // ---- spherical form: treat the eyeball as a globe and light it, so the
    //      whole sclera curves away toward the corners instead of reading flat.
    float Rg = 1.9;                                  // eyeball radius (eye units)
    float zz = sqrt(max(0.0, Rg*Rg - dot(q,q)));
    vec3  N  = normalize(vec3(q, zz));               // sphere surface normal
    vec3  L  = normalize(vec3(-0.35*xs, 0.42, 0.85));   // key light, upper-left/front
    float diff = clamp(dot(N, L), 0.0, 1.0);
    float form = 0.32 + 0.68*diff;                   // shaded globe
    form *= 1.0 - 0.42*smoothstep(0.35, 1.02, length(q)/Rg);   // rim recedes
    // socket depth: the lids occlude the globe at the eye's margin
    float edgeDist = min(archTop - q.y, q.y - archBot);
    form *= mix(0.5, 1.0, smoothstep(0.0, 0.28, edgeDist));

    // ---- iris/pupil geometry (iris tracks with gaze) ----
    vec2 gaze = uGaze*uGazeScale*vec2(0.30,0.20);
    vec2 pIris = q - gaze;
    float r = length(pIris);

    // corneal dome refraction — makes the iris sit under glass
    float corneaR = 0.60;
    vec2 refr = pIris;
    if(r < corneaR){
        float h = sqrt(max(0.0, corneaR*corneaR - r*r));
        refr = pIris*(1.0 - 0.16*h);
    }
    float ri = length(refr);
    float ang = atan(refr.y, refr.x);
    float irisR = 0.46;
    float irn = ri/irisR;                 // 0 center .. 1 limbus

    // ---- sclera ----
    vec3 sclera = vec3(0.90,0.86,0.83);
    sclera *= 1.0 - 0.20*smoothstep(0.1,0.9, q.y);            // upper-lid shadow
    sclera = mix(sclera, vec3(0.95,0.83,0.80), 0.25);        // faint warm/pink cast
    // bloodshot vessels: sparse branching filaments that run roughly radially
    // (in from the corners toward the iris), thinning as they approach the limbus.
    float ea = atan(q.y, q.x);
    float er = length(q);
    vec2  vpol = vec2(ea*2.2, er*2.4);                        // angle,radius space
    float vwarp = fbm(vpol*1.3 + 4.0);
    float vf1 = fbm(vpol*1.6 + vwarp*1.9);                    // few main vessels
    float vf2 = fbm(vpol*3.1 + vwarp*1.5 + 9.0);             // finer capillaries
    float fromCorner = smoothstep(0.34,1.1, abs(q.x));       // 0 center .. 1 corners
    float notIris    = smoothstep(irisR*1.02, irisR*1.4, r);
    float taper      = mix(0.35, 1.0, notIris);              // thinner near iris
    float vein = smoothstep(0.016,0.0, abs(vf1-0.5))
               + 0.45*smoothstep(0.010,0.0, abs(vf2-0.5));
    vein = clamp(vein*fromCorner*notIris*taper, 0.0, 1.0);
    sclera = mix(sclera, vec3(0.74,0.17,0.14), vein*0.7);

    // ---- iris ----
    float fib  = fbm(vec2(ang*7.0, irn*3.0));
    float fib2 = fbm(vec2(ang*23.0, irn*6.0));
    float crypt= fbm(vec2(ang*4.0, irn*4.0)+7.3);
    vec3 inC = vec3(0.22,0.45,0.55);
    vec3 outC= vec3(0.05,0.16,0.26);
    vec3 iris = mix(inC, outC, smoothstep(0.0,1.0, irn));
    iris *= 0.70 + 0.55*fib;
    iris *= 0.85 + 0.30*fib2;
    iris = mix(iris, iris*0.5, smoothstep(0.55,0.9, crypt));
    iris *= 1.0 + 0.22*smoothstep(0.06,0.0, abs(irn-0.42));   // collarette
    iris *= 1.0 - 0.75*smoothstep(0.80,1.0, irn);             // limbal ring

    // pupil (round, dilation-driven), soft edge
    float pr = mix(0.16,0.62, uDilation);
    iris = mix(iris, vec3(0.0), smoothstep(pr, pr-0.02, irn));

    float irisMask = smoothstep(1.0,0.975, irn);
    vec3 eye = mix(sclera, iris, irisMask);

    // apply the spherical form shading to the whole eyeball (speculars added after)
    eye *= form;

    // ---- wet specular highlights (sell the glassiness) ----
    float s1 = smoothstep(0.07,0.0, length(q - vec2(-0.16*xs,0.20)));
    float s2 = smoothstep(0.11,0.0, length(q - vec2(0.12*xs,-0.12)))*0.35;
    eye += (s1+s2)*vec3(1.0);
    // subtle darken at the corneal rim for depth
    eye *= 1.0 - 0.22*smoothstep(corneaR*0.85, corneaR, r)*step(r,corneaR);

    // ---- faint moist rim: thin wet catchlight hugging the lid margins,
    //      strongest along the lower lid (where the tear film pools). ----
    float lowerWet = smoothstep(0.055, 0.0, q.y - archBot) * smoothstep(1.5, 0.85, abs(q.x));
    float upperWet = smoothstep(0.04, 0.0, archTop - q.y) * smoothstep(1.35, 0.85, abs(q.x));
    float wet = (lowerWet + 0.5*upperWet) * smoothstep(0.15, 0.5, open_e);
    eye += wet * 0.20 * vec3(1.0, 0.99, 0.97);

    // ---- eyelid skin: soft flesh, fold crease, lash line, socket seam ----
    vec3 col = eye*lid;
    if(outside > -0.05){
        float odist = length(max(vec2(abs(q.x)-lidW, max(q.y-archTop, archBot-q.y)), vec2(0.0)));
        float inX = smoothstep(lidW+0.22, lidW-0.06, abs(q.x));
        float fall = smoothstep(0.55, 0.02, odist);
        float tex = 0.85 + 0.15*fbm(q*vec2(6.0,7.0)+3.0);
        vec3 skin = vec3(0.29,0.19,0.15) * tex * fall;
        float dT = q.y - archTop;
        float dB = archBot - q.y;
        float crease = smoothstep(0.06,0.11, dT) * smoothstep(0.20,0.13, dT) * inX;
        skin *= 1.0 - (0.45+0.25*angry)*crease;
        float lash = step(0.0,dT) * smoothstep(0.05,0.008, dT) * inX;
        skin *= 1.0 - 0.85*lash;
        skin *= 1.0 - 0.35*angry*smoothstep(0.10,0.35, dT);
        float lowRidge = step(0.0,dB) * smoothstep(0.10,0.03, dB) * inX;
        skin += vec3(0.14,0.09,0.07)*lowRidge*fall;
        float seam = smoothstep(0.022,0.0, odist) * inX;
        skin *= 1.0 - 0.55*seam;
        col += skin*(1.0-lid);
    }
    fragColor = vec4(col, 1.0);
}
"""

WALKER_FRAG = """\
uniform vec2  iResolution;
uniform float iTime;
uniform vec2  uGaze;
uniform float uBlink;
uniform float uDilation;
uniform float uGlow;
uniform vec2  uCenter;
uniform float uEyeR;
uniform float uMirror;
uniform float uOpen;      // lid aperture: squint..wide
uniform float uSlant;     // upper-lid tilt (anger)
uniform vec2  uGazeScale; // multiplies gaze travel (from config)
out vec4 fragColor;
void main(){
    vec2 center = uCenter*iResolution;
    vec2 q = (gl_FragCoord.xy - center)/uEyeR;   // eye space, y up
    float xs = (uMirror>0.5)? -1.0 : 1.0;
    float blink = clamp(uBlink,0.0,1.0);
    float openU = clamp(uOpen, 0.0, 1.6);
    float slM = clamp(uSlant,-1.0,1.0);
    float sl = slM * xs;
    float angry = max(slM, 0.0);

    // ---- stern, hooded aperture (emotion-aware) ----
    float open_e = openU * (1.0 - blink);
    float lidW = 1.15;
    float nx = clamp(q.x/lidW, -1.0, 1.0);
    float baseA = pow(max(0.0,1.0-nx*nx), 0.58);
    float baseL = pow(max(0.0,1.0-nx*nx), 0.82);
    float archTop =  baseA*open_e*(0.50 - 0.08*angry) - sl*0.20*baseA*nx*open_e;
    float archBot = -baseL*open_e*(0.46 - 0.05*angry);
    float lid = smoothstep(0.0,0.024, archTop-q.y)
              * smoothstep(0.0,0.024, q.y-archBot)
              * smoothstep(0.0,0.02, lidW-abs(q.x));
    float outside = max(max(q.y-archTop, archBot-q.y), abs(q.x)-lidW);
    float odist = length(max(vec2(abs(q.x)-lidW, max(q.y-archTop, archBot-q.y)), vec2(0.0)));
    if(odist > 0.70){ fragColor=vec4(0.0,0.0,0.0,1.0); return; }
    float edgeIn = min(archTop-q.y, q.y-archBot);

    // ---- the eyeball: a frosted globe crowded by the lids ----
    // Everything in this block is multiplied by `lid` (0 outside the
    // aperture), and the sclera / iris are each hidden where the other is
    // fully opaque, so each layer is only computed where it can show.
    // Same picture, far fewer noise evaluations per pixel.
    vec3 inner = vec3(0.0);
    if(lid > 0.0){
        // ---- the eyeball: a frosted globe crowded by the lids ----
        vec2 gaze = uGaze*uGazeScale*vec2(0.30,0.18);
        vec2 pI = q - gaze;
        float rr = length(pI);
        float ang = atan(pI.y, pI.x);
        float irisR = 0.58;
        float irn = rr/irisR;

        // spherical form so the eyeball reads round inside the socket
        float Rg = 1.7;
        float zz = sqrt(max(0.0, Rg*Rg - dot(q,q)));
        vec3  N  = normalize(vec3(q, zz));
        float diffl = clamp(dot(N, normalize(vec3(-0.30*xs, 0.40, 0.85))), 0.0, 1.0);
        float form = 0.35 + 0.65*diffl;
        float socket = 0.45 + 0.55*smoothstep(0.0,0.26, edgeIn);    // deep recess
        socket *= 0.55 + 0.45*smoothstep(0.0,0.30, archTop-q.y);    // hooded

        vec3 sclera = vec3(0.0);
        if(irn > 0.955){                 // sclera visible (iris blend < 1)
            // frosted sclera: icy mottled surface catching the iris glow
            float fr  = fbm(q*vec2(7.0,8.0)+11.0);
            float fr2 = fbm(q*vec2(16.0,18.0)+23.0);
            sclera = vec3(0.17,0.21,0.27) * (0.45+0.70*fr) * (0.80+0.30*fr2);
            sclera += vec3(0.12,0.30,0.60)*exp(-pow(max(rr-irisR,0.0)*1.6,2.0))*uGlow;
            sclera *= form * socket;

        }
        vec3 iris = vec3(0.0);
        if(irn < 1.0){                   // iris visible (blend > 0)
            // luminous ice iris: gradient + crystalline shards + cellular mottling
            vec3 coreC = vec3(0.90,0.98,1.00);
            vec3 midC  = vec3(0.20,0.55,1.00);
            vec3 edgeC = vec3(0.02,0.10,0.35);
            iris = mix(coreC, midC, smoothstep(0.06,0.42, irn));
            iris = mix(iris, edgeC, smoothstep(0.46,1.00, irn));
            float f1 = vnoise(vec2(ang*22.0, irn*3.0));
            float f2 = fbm(vec2(ang*9.0, irn*4.0)+5.0);
            float shard = pow(1.0-abs(2.0*f1-1.0), 3.0);
            float cell  = smoothstep(0.75,0.20, voro(pI*11.0+4.0));
            iris *= 0.55 + 0.28*f2 + 0.45*shard;
            iris *= 0.78 + 0.22*cell;
            iris *= 1.0 - 0.55*smoothstep(0.86,1.0, irn);
            iris *= 1.0 + 0.9*exp(-irn*irn*6.0);
            iris *= (0.9 + 0.5*uGlow);
            iris *= 0.75 + 0.25*form;

            float d = clamp(uDilation,0.0,1.0);
            float pr = 0.13 + 0.27*d;
            iris = mix(iris, vec3(0.0,0.012,0.05), smoothstep(pr, pr-0.05, irn));

        }
        inner = mix(sclera, iris, smoothstep(1.0,0.955, irn));

        // ---- cold fire: blue flames licking out from the iris edge ----
        float fd = (rr - irisR)/0.30;                 // 0 at limbus -> 1 at flame tips
        if(fd > -0.6 && fd < 1.1){
            vec2 dirv = pI/max(rr,1e-3);
            // Flames advect outward forever without the sample offset growing
            // without bound (that shears the tongues into spikes over time and
            // eventually loses float precision). Two layers each ramp over one
            // period and restart; their phases are half a period apart and the
            // cross-fade weight is zero exactly where each one restarts, so the
            // discontinuity is never visible.
            float T   = 11.0;                       // seconds per layer cycle
            float ph  = fract(iTime/T);             // 0..1
            float ph2 = fract(ph + 0.5);
            float wA  = 1.0 - abs(2.0*ph  - 1.0);   // 0 at ph=0 and ph=1
            float wB  = 1.0 - abs(2.0*ph2 - 1.0);   // 0 at ph2=0 (i.e. ph=0.5)
            float travel = 3.0;
            float fnA = fbm(pI*5.0  - dirv*(ph *travel))*0.75
                      + fbm(pI*10.0 - dirv*(ph *travel*1.85))*0.45;
            float fnB = fbm(pI*5.0  - dirv*(ph2*travel))*0.75
                      + fbm(pI*10.0 - dirv*(ph2*travel*1.85))*0.45;
            float fn  = (fnA*wA + fnB*wB) / max(wA+wB, 1e-4);
            float tongues = smoothstep(0.24, 0.62, fn - clamp(fd,0.0,1.0))
                          * smoothstep(-0.08, 0.10, fd)
                          * smoothstep(1.05, 0.30, fd);
            float ringf = exp(-pow((rr-irisR)*8.0, 2.0));
            float fire = tongues*0.85 + ringf*0.22;
            vec3 fireC = mix(vec3(0.10,0.40,1.0), vec3(0.72,0.92,1.0),
                             clamp(ringf*0.6 + tongues*0.25, 0.0, 1.0));
            inner += fireC * fire * (0.55 + 0.45*uGlow) * 0.85;
        }
        // wet cornea: soft window + crisp dot, fixed in eye space (real reflections)
        float s1 = smoothstep(0.075,0.012, length(max(abs(q-vec2(-0.13*xs,0.13))-vec2(0.040,0.028),vec2(0.0))));
        float s2 = smoothstep(0.028,0.0, length(q-vec2(0.06*xs,-0.05)))*0.55;
        inner += (s1+s2)*vec3(0.92,0.97,1.0)*clamp(open_e,0.0,1.0);

    }
    // cold halo spilling from the socket
    float lit = uGlow * (0.25 + 0.75*clamp(open_e,0.0,1.0));
    vec3 halo = vec3(0.10,0.30,0.75) * exp(-pow(length(q)*1.5, 2.0)) * lit * 0.9;
    vec3 col = inner*lid + halo*0.30*(1.0-lid);

    // ---- frozen, crevassed skin around the socket ----
    if(outside > -0.05){
        float inX = smoothstep(lidW+0.24, lidW-0.06, abs(q.x));
        float fall = smoothstep(0.66, 0.04, odist);
        // radiating wrinkles/crevasses
        float wr = fbm(vec2(q.x*2.6, odist*8.0)+3.0);   // concentric crevasses, no seam
        float ridge = 1.0 - abs(2.0*wr-1.0);
        float tex = 0.58 + 0.42*pow(ridge, 1.7);
        vec3 skin = vec3(0.24,0.29,0.35) * tex * fall;
        // frost sparkle
        float sp = step(0.9955, hash21(floor(q*95.0)+2.0));
        skin += vec3(0.45,0.55,0.65)*sp*fall*0.6;
        float dT = q.y - archTop;
        // heavy dark brow ridge, deeper when angry
        float brow = smoothstep(0.04,0.12, dT) * smoothstep(0.34,0.16, dT) * inX;
        skin *= 1.0 - (0.50+0.30*angry)*brow;
        skin *= 1.0 - 0.30*angry*smoothstep(0.10,0.40, dT);
        // dark margin line
        float lash = step(0.0,dT) * smoothstep(0.045,0.008, dT) * inX;
        skin *= 1.0 - 0.75*lash;
        // deep socket recess all around
        skin *= 1.0 - 0.65*smoothstep(0.12,0.0, odist);
        // faint cold rim light from the eye onto the lower socket
        float below = smoothstep(0.18,-0.28, q.y-(archBot+archTop)*0.5);
        float rimBand = smoothstep(0.02,0.05, odist)*smoothstep(0.16,0.06, odist);
        skin += vec3(0.10,0.22,0.42)*rimBand*mix(0.10,0.55,below)*lit*inX;
        col += skin*(1.0-lid);
    }

    fragColor = vec4(col, 1.0);
}
"""

EYE_SHADERS = {"demon": DEMON_FRAG, "human": HUMAN_FRAG, "walker": WALKER_FRAG}
