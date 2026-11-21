/* sounar_upmix: Sounar's upmixer for the room layout, a LADSPA plugin for PipeWire's filter-graph.
 * See docs/upmix.md.
 *
 * One instance per speaker chain. Stereo in; two outputs for this chain:
 *   Out = bass + its share of the room mix + the direct sounds placed at it
 *   Amb = its ambience (surround speakers; the graph adds the ambience band, dip and delay)
 *
 * Every chain's direction and role (up to 8) comes in as controls, so each instance knows the
 * whole speaker map and renders only its own chain ("Self").
 *
 * Each frame (2048-point STFT, hop 512, sqrt-Hann) and each frequency bin is split into:
 *   - bass below the crossover: mono, at this chain's bass gain
 *   - base: the room layout's own mix for this chain (mid/side gains, vocal lift on M, or the
 *     ambience gain for a surround): Placement 0 reproduces the room layout
 *   - placed: the part common to both channels is a direct sound; its left-right position picks a
 *     direction on the stage between the outermost front speakers (centre = straight ahead), and
 *     it plays from the two speakers around that direction (constant power); Wrap carries the
 *     stage edges towards a neighbouring speaker (one within 120 degrees). The rest is ambience:
 *     a share stays in the front speakers (left ones the left channel's, right ones the right's),
 *     the rest goes to the surrounds, with the polarity of their room-layout ambience.
 * out = bass + (1 - Placement) * base + Placement * m * placed, where m (slow, 3 s) keeps the
 * chain as loud as the base makes it, so placing sounds never starves a speaker.
 * Latency: 2048 samples (reported on the Latency port), the same on every chain.
 * No allocation in run(); any block size; NaN and inf in are silence.
 */
#include <math.h>
#include <stdlib.h>
#include <string.h>

/* ---- LADSPA 1.1 interface (the ABI, written out so no header is needed) ---- */
typedef float LADSPA_Data;
typedef int LADSPA_Properties;
typedef int LADSPA_PortDescriptor;
typedef int LADSPA_PortRangeHintDescriptor;
typedef void *LADSPA_Handle;
#define LADSPA_PROPERTY_HARD_RT_CAPABLE 0x4
#define LADSPA_PORT_INPUT 0x1
#define LADSPA_PORT_OUTPUT 0x2
#define LADSPA_PORT_CONTROL 0x4
#define LADSPA_PORT_AUDIO 0x8
#define LADSPA_HINT_BOUNDED_BELOW 0x1
#define LADSPA_HINT_BOUNDED_ABOVE 0x2
typedef struct { LADSPA_PortRangeHintDescriptor HintDescriptor; LADSPA_Data LowerBound, UpperBound; } LADSPA_PortRangeHint;
typedef struct _LADSPA_Descriptor {
    unsigned long UniqueID;
    const char *Label;
    LADSPA_Properties Properties;
    const char *Name, *Maker, *Copyright;
    unsigned long PortCount;
    const LADSPA_PortDescriptor *PortDescriptors;
    const char *const *PortNames;
    const LADSPA_PortRangeHint *PortRangeHints;
    void *ImplementationData;
    LADSPA_Handle (*instantiate)(const struct _LADSPA_Descriptor *, unsigned long);
    void (*connect_port)(LADSPA_Handle, unsigned long, LADSPA_Data *);
    void (*activate)(LADSPA_Handle);
    void (*run)(LADSPA_Handle, unsigned long);
    void (*run_adding)(LADSPA_Handle, unsigned long);
    void (*set_run_adding_gain)(LADSPA_Handle, LADSPA_Data);
    void (*deactivate)(LADSPA_Handle);
    void (*cleanup)(LADSPA_Handle);
} LADSPA_Descriptor;

/* ---- ports ---- */
#define MAXCH 8
enum {
    P_IN_L, P_IN_R, P_OUT, P_AMB,
    P_PLACE, P_WRAP, P_AMB_FRONT, P_XOVER, P_VOCAL, P_MATCH, P_SELF, P_COUNT,
    P_ANG, P_ZONE = P_ANG + MAXCH,
    P_BASS = P_ZONE + MAXCH, P_BASE_M, P_BASE_S, P_BASE_AMB,
    P_LATENCY, NPORTS
};
#define FIRST_CONTROL P_PLACE
enum { ZONE_NONE = 0, ZONE_FRONT = 1, ZONE_SURROUND = 2 };

static const char *const port_names[NPORTS] = {
    "In L", "In R", "Out", "Amb",
    "Placement", "Wrap", "Ambience front", "Crossover", "Vocal", "Match", "Self", "Count",
    "Angle 1", "Angle 2", "Angle 3", "Angle 4", "Angle 5", "Angle 6", "Angle 7", "Angle 8",
    "Zone 1", "Zone 2", "Zone 3", "Zone 4", "Zone 5", "Zone 6", "Zone 7", "Zone 8",
    "Bass", "Base M", "Base S", "Base amb", "Latency",
};
#define AI (LADSPA_PORT_INPUT | LADSPA_PORT_AUDIO)
#define AO (LADSPA_PORT_OUTPUT | LADSPA_PORT_AUDIO)
#define CI (LADSPA_PORT_INPUT | LADSPA_PORT_CONTROL)
static const LADSPA_PortDescriptor port_desc[NPORTS] = {
    AI, AI, AO, AO,
    [P_PLACE ... P_BASE_AMB] = CI,
    [P_LATENCY] = LADSPA_PORT_OUTPUT | LADSPA_PORT_CONTROL,
};
#define B (LADSPA_HINT_BOUNDED_BELOW | LADSPA_HINT_BOUNDED_ABOVE)
static const LADSPA_PortRangeHint port_hints[NPORTS] = {
    [P_PLACE] = {B, 0, 1}, [P_WRAP] = {B, 0, 1}, [P_AMB_FRONT] = {B, 0, 1}, [P_XOVER] = {B, 40, 400},
    [P_VOCAL] = {B, -12, 12}, [P_MATCH] = {B, 0, 1}, [P_SELF] = {B, 1, MAXCH}, [P_COUNT] = {B, 1, MAXCH},
    [P_ANG ... P_ANG + MAXCH - 1] = {B, -180, 180}, [P_ZONE ... P_ZONE + MAXCH - 1] = {B, 0, 2},
    [P_BASS] = {B, 0, 4}, [P_BASE_M] = {B, -4, 4}, [P_BASE_S] = {B, -4, 4}, [P_BASE_AMB] = {B, -4, 4},
};
static const float ctl_default[NPORTS] = {
    [P_PLACE] = 0.6f, [P_WRAP] = 0, [P_AMB_FRONT] = 0.5f, [P_XOVER] = 150, [P_VOCAL] = 0, [P_MATCH] = 1,
    [P_SELF] = 1, [P_COUNT] = 2, [P_ANG] = -30, [P_ANG + 1] = 30, [P_ZONE] = 1, [P_ZONE + 1] = 1,
    [P_BASS] = 1, [P_BASE_M] = 1, [P_BASE_S] = 0, [P_BASE_AMB] = 0,
};

/* ---- STFT ---- */
#define N 2048
#define HOP 512
#define LAT (N - HOP)              /* where new input goes in the frame buffer */
#define LATENCY N                  /* a sample is complete after its 4th frame, read one hop later */
#define K (N / 2 + 1)
#define LUT 721                    /* placement gain for this chain, every 1/360 of the stage */
#define WRAP_MAX_ARC 120.0
#define COH_BIAS 0.12              /* ~1/Neff of the cross-spectrum averaging */

typedef struct {
    LADSPA_Data *port[NPORTS];
    double rate;
    long rover;
    float in_l[N], in_r[N];
    float fifo[2][HOP];
    float accum[2][N + HOP];
    float win[N];
    float cos_t[N / 2], sin_t[N / 2];
    int bitrev[N];
    float re[N], im[N];
    float lr_[K], li_[K], rr_[K], ri_[K];
    double pll[K], prr[K], plr_re[K], plr_im[K];
    float raw_ll[K], raw_rr[K], raw_re[K], raw_im[K];
    float lp[K], hp[K], vocal[K];
    float yo_re[K], yo_im[K], ya_re[K], ya_im[K];
    float gain_lut[LUT];
    double eb, ep, m;
    long frames;
    float c[NPORTS];               /* controls, read and clamped once per frame */
    float last_geo[2 * MAXCH + 3], last_filt[2];
} Upmix;

static void fft(Upmix *u, float *re, float *im) {
    for (int i = 0; i < N; i++) {
        int j = u->bitrev[i];
        if (j > i) { float t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (int len = 2; len <= N; len <<= 1) {
        int half = len >> 1, step = N / len;
        for (int s = 0; s < N; s += len)
            for (int k = 0; k < half; k++) {
                float wr = u->cos_t[k * step], wi = -u->sin_t[k * step];
                int a = s + k, b = a + half;
                float tr = re[b] * wr - im[b] * wi, ti = re[b] * wi + im[b] * wr;
                re[b] = re[a] - tr; im[b] = im[a] - ti;
                re[a] += tr; im[a] += ti;
            }
    }
}

static void ifft(Upmix *u, float *re, float *im) {   /* unnormalised */
    for (int i = 0; i < N; i++) im[i] = -im[i];
    fft(u, re, im);
    for (int i = 0; i < N; i++) im[i] = -im[i];
}

static double peaking_mag(double f, double f0, double gain_db, double q, double fs) {
    if (fabs(gain_db) < 1e-6 || f0 >= fs / 2) return 1.0;
    double A = pow(10.0, gain_db / 40.0), w0 = 2 * M_PI * f0 / fs, al = sin(w0) / (2 * q);
    double b0 = 1 + al * A, b1 = -2 * cos(w0), b2 = 1 - al * A;
    double a0 = 1 + al / A, a1 = -2 * cos(w0), a2 = 1 - al / A;
    double w = 2 * M_PI * f / fs, c1 = cos(w), s1 = sin(w), c2 = cos(2 * w), s2 = sin(2 * w);
    double nr = b0 + b1 * c1 + b2 * c2, ni = -(b1 * s1 + b2 * s2);
    double dr = a0 + a1 * c1 + a2 * c2, di = -(a1 * s1 + a2 * s2);
    return sqrt((nr * nr + ni * ni) / (dr * dr + di * di + 1e-30));
}

static double wrap180(double a) { while (a > 180) a -= 360; while (a <= -180) a += 360; return a; }
static double cw(double from, double to) { double d = to - from; while (d < 0) d += 360; while (d >= 360) d -= 360; return d; }

/* Placement gain of chain `self` for every left-right position. Stage: the front chains, from
 * the leftmost to the rightmost going through straight ahead; centre = ahead when it lies on the
 * stage, else its middle. Wrap extends each edge towards the next chain (front or surround) if it
 * is within WRAP_MAX_ARC degrees. A direction plays from the two chains whose arc holds it. */
static void build_lut(Upmix *u) {
    int n = (int)lroundf(u->c[P_COUNT]), self = (int)lroundf(u->c[P_SELF]) - 1;
    double ang[MAXCH];
    int zone[MAXCH], used[MAXCH], nu = 0, front[MAXCH], nf = 0;
    for (int i = 0; i < n; i++) {
        ang[i] = wrap180(u->c[P_ANG + i]);
        zone[i] = (int)lroundf(u->c[P_ZONE + i]);
        if (zone[i] == ZONE_FRONT || zone[i] == ZONE_SURROUND) used[nu++] = i;
        if (zone[i] == ZONE_FRONT) front[nf++] = i;
    }
    for (int i = 0; i < LUT; i++) u->gain_lut[i] = 0;
    if (nf == 0 || self < 0 || self >= n) goto done;
    if (nf == 1) {                                     /* one front speaker carries the whole stage */
        if (front[0] == self) for (int i = 0; i < LUT; i++) u->gain_lut[i] = 1;
        goto done;
    }
    /* the stage: the largest gap between front chains (going clockwise) is behind it */
    int order[MAXCH];
    for (int i = 0; i < nf; i++) order[i] = front[i];
    for (int i = 0; i < nf; i++)
        for (int j = i + 1; j < nf; j++)
            if (ang[order[j]] < ang[order[i]]) { int t = order[i]; order[i] = order[j]; order[j] = t; }
    int gap_at = 0;
    double gap = -1;
    for (int i = 0; i < nf; i++) {
        double g = cw(ang[order[i]], ang[order[(i + 1) % nf]]);
        if (nf == 1) g = 360;
        if (g > gap) { gap = g; gap_at = i; }
    }
    double left = ang[order[(gap_at + 1) % nf]], right = ang[order[gap_at]];
    double span = cw(left, right);
    double centre = cw(left, 0) <= span ? 0.0 : wrap180(left + span / 2);
    double toL = cw(left, centre), toR = cw(centre, right);
    /* wrap: the nearest used chain beyond each edge */
    double extL = 0, extR = 0, w = u->c[P_WRAP];
    {
        double bestL = 360, bestR = 360;
        for (int k = 0; k < nu; k++) {
            int i = used[k];
            double dl = cw(ang[i], left), dr = cw(right, ang[i]);
            if (dl > 1e-6 && dl < bestL && cw(left, ang[i]) > span) bestL = dl;
            if (dr > 1e-6 && dr < bestR && cw(left, ang[i]) > span) bestR = dr;
        }
        if (bestL <= WRAP_MAX_ARC) extL = w * bestL;
        if (bestR <= WRAP_MAX_ARC) extR = w * bestR;
    }
    /* the ring of used chains, by angle */
    int ring[MAXCH];
    for (int i = 0; i < nu; i++) ring[i] = used[i];
    for (int i = 0; i < nu; i++)
        for (int j = i + 1; j < nu; j++)
            if (ang[ring[j]] < ang[ring[i]]) { int t = ring[i]; ring[i] = ring[j]; ring[j] = t; }
    for (int li = 0; li < LUT; li++) {
        double p = -1.0 + 2.0 * li / (LUT - 1);
        double th = p < 0 ? centre + p * (toL + extL) : centre + p * (toR + extR);
        th = wrap180(th);
        for (int i = 0; i < nu; i++) {
            int a = ring[i], b = ring[(i + 1) % nu];
            double arc = nu == 1 ? 360 : cw(ang[a], ang[b]);
            double t = cw(ang[a], th);
            if (t <= arc + 1e-9) {
                double frac = arc > 1e-9 ? t / arc : 0;
                if (frac > 1) frac = 1;
                if (a == self) u->gain_lut[li] += (float)cos(frac * M_PI / 2);
                if (b == self) u->gain_lut[li] += (float)sin(frac * M_PI / 2);
                break;
            }
        }
    }
done:
    for (int i = 0; i < 2 * MAXCH + 3; i++) u->last_geo[i] = -1e9f;
    for (int i = 0; i < MAXCH; i++) { u->last_geo[i] = u->c[P_ANG + i]; u->last_geo[MAXCH + i] = u->c[P_ZONE + i]; }
    u->last_geo[2 * MAXCH] = u->c[P_WRAP];
    u->last_geo[2 * MAXCH + 1] = u->c[P_SELF];
    u->last_geo[2 * MAXCH + 2] = u->c[P_COUNT];
}

static int geo_changed(Upmix *u) {
    for (int i = 0; i < MAXCH; i++)
        if (u->last_geo[i] != u->c[P_ANG + i] || u->last_geo[MAXCH + i] != u->c[P_ZONE + i]) return 1;
    return u->last_geo[2 * MAXCH] != u->c[P_WRAP] || u->last_geo[2 * MAXCH + 1] != u->c[P_SELF]
        || u->last_geo[2 * MAXCH + 2] != u->c[P_COUNT];
}

static void build_filters(Upmix *u) {
    double fc = u->c[P_XOVER], v = u->c[P_VOCAL];
    for (int k = 0; k < K; k++) {
        double f = k * u->rate / N, r = f / fc, r4 = r * r * r * r;
        u->lp[k] = (float)(1.0 / (1.0 + r4));          /* zero-phase Linkwitz-Riley: lp + hp = 1 */
        u->hp[k] = 1.0f - u->lp[k];
        u->vocal[k] = fabs(v) >= 0.05
            ? (float)(peaking_mag(f, 2500, v, 0.8, u->rate) * peaking_mag(f, 900, 0.4 * v, 1.0, u->rate))
            : 1.0f;                                     /* = the room layout's vocal lift */
    }
    u->last_filt[0] = u->c[P_XOVER];
    u->last_filt[1] = u->c[P_VOCAL];
}

static void read_controls(Upmix *u) {
    for (int p = FIRST_CONTROL; p < P_LATENCY; p++) {
        float v = u->port[p] ? *u->port[p] : ctl_default[p];
        if (!isfinite(v)) v = ctl_default[p];
        float lo = port_hints[p].LowerBound, hi = port_hints[p].UpperBound;
        if (v < lo) v = lo;
        if (v > hi) v = hi;
        u->c[p] = v;
    }
}

static void reset(Upmix *u) {
    memset(u->in_l, 0, sizeof u->in_l); memset(u->in_r, 0, sizeof u->in_r);
    memset(u->fifo, 0, sizeof u->fifo); memset(u->accum, 0, sizeof u->accum);
    memset(u->pll, 0, sizeof u->pll); memset(u->prr, 0, sizeof u->prr);
    memset(u->plr_re, 0, sizeof u->plr_re); memset(u->plr_im, 0, sizeof u->plr_im);
    u->eb = u->ep = 0; u->m = 1;
    u->rover = LAT;
    u->frames = 0;
}

static void process_frame(Upmix *u) {
    read_controls(u);
    if (u->c[P_XOVER] != u->last_filt[0] || u->c[P_VOCAL] != u->last_filt[1]) build_filters(u);
    if (geo_changed(u)) build_lut(u);

    for (int n = 0; n < N; n++) { u->re[n] = u->in_l[n] * u->win[n]; u->im[n] = u->in_r[n] * u->win[n]; }
    fft(u, u->re, u->im);
    for (int k = 0; k < K; k++) {                       /* unpack L and R; raw cross-spectra */
        int nk = (N - k) % N;
        float zr = u->re[k], zi = u->im[k], cr = u->re[nk], ci = -u->im[nk];
        float lr = 0.5f * (zr + cr), li = 0.5f * (zi + ci);
        float rr = 0.5f * (zi - ci), ri = -0.5f * (zr - cr);
        u->lr_[k] = lr; u->li_[k] = li; u->rr_[k] = rr; u->ri_[k] = ri;
        u->raw_ll[k] = lr * lr + li * li;
        u->raw_rr[k] = rr * rr + ri * ri;
        u->raw_re[k] = lr * rr + li * ri;
        u->raw_im[k] = li * rr - lr * ri;
    }
    double al = exp(-(double)HOP / (0.060 * u->rate));     /* ~60 ms, +-2 bins */
    for (int k = 0; k < K; k++) {
        double ll = 0, rr = 0, xr = 0, xi = 0, ws = 0;
        for (int j = -2; j <= 2; j++) {
            int b = k + j;
            if (b < 0 || b >= K) continue;
            double w = 3 - abs(j);
            ll += w * u->raw_ll[b]; rr += w * u->raw_rr[b]; xr += w * u->raw_re[b]; xi += w * u->raw_im[b];
            ws += w;
        }
        ll /= ws; rr /= ws; xr /= ws; xi /= ws;
        u->pll[k] = al * u->pll[k] + (1 - al) * ll;
        u->prr[k] = al * u->prr[k] + (1 - al) * rr;
        u->plr_re[k] = al * u->plr_re[k] + (1 - al) * xr;
        u->plr_im[k] = al * u->plr_im[k] + (1 - al) * xi;
        if (u->pll[k] < 1e-30) u->pll[k] = 0;
        if (u->prr[k] < 1e-30) u->prr[k] = 0;
        if (fabs(u->plr_re[k]) < 1e-30) u->plr_re[k] = 0;
        if (fabs(u->plr_im[k]) < 1e-30) u->plr_im[k] = 0;
    }

    const int self = (int)lroundf(u->c[P_SELF]) - 1;
    const int zone = (self >= 0 && self < MAXCH) ? (int)lroundf(u->c[P_ZONE + self]) : ZONE_FRONT;
    const double sang = (self >= 0 && self < MAXCH) ? wrap180(u->c[P_ANG + self]) : 0;
    const float x = u->c[P_PLACE], keep = u->c[P_AMB_FRONT];
    const float ka = sqrtf(keep), kr = sqrtf(1 - keep);
    const float bass = u->c[P_BASS], bm = u->c[P_BASE_M], bs = u->c[P_BASE_S], bam = u->c[P_BASE_AMB];
    const float pol = bam < 0 ? -1.0f : 1.0f;
    const int side = sang < -5 ? -1 : (sang > 5 ? 1 : 0);   /* which channel's ambience a front keeps */
    const float mm = (float)u->m;
    double eb = 0, ep = 0;

    for (int k = 0; k < K; k++) {
        float lr = u->lr_[k], li = u->li_[k], rr = u->rr_[k], ri = u->ri_[k];
        float lp = u->lp[k], hp = u->hp[k], v = u->vocal[k];
        float mr = 0.5f * (lr + rr), mi = 0.5f * (li + ri);
        float sr = 0.5f * (lr - rr), si = 0.5f * (li - ri);
        float mlr = mr * lp, mli = mi * lp;
        float shr = sr * hp, shi = si * hp;
        float lhr = lr * hp, lhi = li * hp, rhr = rr * hp, rhi = ri * hp;

        float boR = bm * mr * hp * v + bs * shr, boI = bm * mi * hp * v + bs * shi;   /* base */
        float baR = bam * shr, baI = bam * shi;

        /* L = gl*s + aL, R = gr*s + aR, ambience equally loud in both: its power Pa solves
         * (Pll - Pa)(Prr - Pa) = |Plr|^2; the rest is direct, its level difference the position */
        double ll = u->pll[k], rrp = u->prr[k], xr = u->plr_re[k], xi = u->plr_im[k];
        double mag2 = xr * xr + xi * xi - COH_BIAS * ll * rrp;
        double mag = mag2 > 0 ? sqrt(mag2) : 0, tot2 = ll + rrp, d = 0, pan = 0;
        if (tot2 > 1e-30) {
            double dif = ll - rrp, dif2 = dif * dif - 2 * COH_BIAS * ll * rrp;
            if (dif2 < 0) dif2 = 0;
            double pa = 0.5 * (tot2 - sqrt(dif2 + 4 * mag * mag));
            if (pa < 0) pa = 0;
            double dl = ll - pa, dr = rrp - pa;
            if (dl < 0) dl = 0;
            if (dr < 0) dr = 0;
            d = sqrt((dl + dr) / tot2);
            pan = (dl + dr) > 1e-30 ? (dr - dl) / (dr + dl) : 0;
            double cph = mag > 1e-30 ? xr / sqrt(xr * xr + xi * xi) : 1;
            double bal = 2 * sqrt(ll * rrp) / tot2;        /* anti-phase between equal channels = wide */
            d *= 1 - (1 - 0.5 * (1 + cph)) * bal;
            if (d > 1) d = 1;
        }
        double amb = sqrt(1 - d * d);
        int li_ = (int)lround((pan + 1) * 0.5 * (LUT - 1));
        if (li_ < 0) li_ = 0;
        if (li_ > LUT - 1) li_ = LUT - 1;
        float g = u->gain_lut[li_];
        float sumr = lhr + rhr, sumi = lhi + rhi;
        float el = lhr * lhr + lhi * lhi, er = rhr * rhr + rhi * rhi;
        float smag = sqrtf(sumr * sumr + sumi * sumi), tot = sqrtf(el + er);
        float phr, phi;
        if (smag > 1e-6f * tot && smag > 1e-20f) { phr = sumr / smag; phi = sumi / smag; }
        else if (el >= er && el > 1e-30f) { float a = sqrtf(el); phr = lhr / a; phi = lhi / a; }
        else if (er > 1e-30f) { float a = sqrtf(er); phr = rhr / a; phi = rhi / a; }
        else { phr = 1; phi = 0; }
        double cwt = 1 - 2 * fabs(pan);
        cwt = cwt > 0 ? cwt * cwt : 0;
        float vc = (float)(1 + (v - 1) * cwt);            /* vocal lift: the centre only */
        float dm = (float)d * tot * vc * g;
        float poR = dm * phr, poI = dm * phi, paR = 0, paI = 0;
        float a_ = (float)amb;
        if (zone == ZONE_FRONT) {
            float sl = side < 0 ? 1.0f : (side == 0 ? 0.70710678f : 0.0f);
            float sr_ = side > 0 ? 1.0f : (side == 0 ? 0.70710678f : 0.0f);
            poR += ka * a_ * (sl * lhr + sr_ * rhr);
            poI += ka * a_ * (sl * lhi + sr_ * rhi);
        } else if (zone == ZONE_SURROUND) {
            paR = pol * kr * 0.70710678f * a_ * (lhr - rhr);
            paI = pol * kr * 0.70710678f * a_ * (lhi - rhi);
        }
        float wgt = (k == 0 || k == K - 1) ? 1.0f : 2.0f;
        eb += wgt * (boR * boR + boI * boI + baR * baR + baI * baI);
        ep += wgt * (poR * poR + poI * poI + paR * paR + paI * paI);

        float wb = 1 - x, wp = x * mm;
        u->yo_re[k] = bass * mlr + wb * boR + wp * poR;  u->yo_im[k] = bass * mli + wb * boI + wp * poI;
        u->ya_re[k] = wb * baR + wp * paR;               u->ya_im[k] = wb * baI + wp * paI;
    }

    /* slow level match: the placed part as loud as the base (above the crossover) */
    u->frames++;
    double tau = u->frames < 200 ? 0.5 : 3.0;
    double am = exp(-(double)HOP / (tau * u->rate));
    if (eb > 1e-9 || ep > 1e-9) {
        u->eb = am * u->eb + (1 - am) * eb;
        u->ep = am * u->ep + (1 - am) * ep;
    }
    double m = 1;
    if (u->c[P_MATCH] >= 0.5f && u->eb > 1e-12 && u->ep > 1e-12) m = sqrt(u->eb / u->ep);
    if (m < 0.25) m = 0.25;
    if (m > 4.0) m = 4.0;
    if (!isfinite(m)) m = 1;
    u->m = m;

    /* one inverse FFT for both real outputs: z = Yo + j Ya */
    u->yo_im[0] = u->ya_im[0] = u->yo_im[K - 1] = u->ya_im[K - 1] = 0;
    for (int k = 0; k < K; k++) { u->re[k] = u->yo_re[k] - u->ya_im[k]; u->im[k] = u->yo_im[k] + u->ya_re[k]; }
    for (int k = K; k < N; k++) {
        int q = N - k;
        u->re[k] = u->yo_re[q] + u->ya_im[q];
        u->im[k] = -u->yo_im[q] + u->ya_re[q];
    }
    ifft(u, u->re, u->im);
    const float scale = 1.0f / (N * 2.0f);             /* 1/N for the inverse, 1/2 for the overlap */
    for (int n = 0; n < N; n++) {
        float w = u->win[n] * scale;
        u->accum[0][n] += u->re[n] * w;
        u->accum[1][n] += u->im[n] * w;
    }
    for (int o = 0; o < 2; o++) {
        for (int n = 0; n < HOP; n++) {
            float s = u->accum[o][n];
            u->fifo[o][n] = isfinite(s) ? s : 0.0f;
        }
        memmove(u->accum[o], u->accum[o] + HOP, N * sizeof(float));
        memset(u->accum[o] + N, 0, HOP * sizeof(float));
    }
    memmove(u->in_l, u->in_l + HOP, LAT * sizeof(float));
    memmove(u->in_r, u->in_r + HOP, LAT * sizeof(float));
}

static LADSPA_Handle instantiate(const LADSPA_Descriptor *d, unsigned long rate) {
    (void)d;
    Upmix *u = calloc(1, sizeof *u);
    if (!u) return NULL;
    u->rate = rate > 0 ? (double)rate : 48000.0;
    for (int n = 0; n < N; n++) u->win[n] = (float)sqrt(0.5 * (1 - cos(2 * M_PI * n / N)));
    for (int k = 0; k < N / 2; k++) { u->cos_t[k] = (float)cos(2 * M_PI * k / N); u->sin_t[k] = (float)sin(2 * M_PI * k / N); }
    int bits = 0;
    while ((1 << bits) < N) bits++;
    for (int i = 0; i < N; i++) {
        int r = 0;
        for (int b = 0; b < bits; b++) if (i & (1 << b)) r |= 1 << (bits - 1 - b);
        u->bitrev[i] = r;
    }
    for (int p = FIRST_CONTROL; p < P_LATENCY; p++) u->c[p] = ctl_default[p];
    build_filters(u);
    build_lut(u);
    reset(u);
    return u;
}

static void connect_port(LADSPA_Handle h, unsigned long port, LADSPA_Data *data) {
    if (port < NPORTS) ((Upmix *)h)->port[port] = data;
}

static void activate(LADSPA_Handle h) { reset((Upmix *)h); }

static void run(LADSPA_Handle h, unsigned long count) {
    Upmix *u = h;
    if (u->port[P_LATENCY]) *u->port[P_LATENCY] = (float)LATENCY;
    LADSPA_Data *in_l = u->port[P_IN_L], *in_r = u->port[P_IN_R];
    LADSPA_Data *out[2] = {u->port[P_OUT], u->port[P_AMB]};
    for (unsigned long i = 0; i < count; i++) {
        float l = in_l ? in_l[i] : 0.0f, r = in_r ? in_r[i] : 0.0f;
        if (!(fabsf(l) < 1e3f)) l = 0;
        if (!(fabsf(r) < 1e3f)) r = 0;
        u->in_l[u->rover] = l;
        u->in_r[u->rover] = r;
        long o = u->rover - LAT;
        for (int c = 0; c < 2; c++)
            if (out[c]) out[c][i] = u->fifo[c][o];
        if (++u->rover >= N) {
            process_frame(u);
            u->rover = LAT;
        }
    }
}

static void cleanup(LADSPA_Handle h) { free(h); }

static const LADSPA_Descriptor descriptor = {
    .UniqueID = 0x534e5532,         /* "SNU2" */
    .Label = "sounar_upmix",
    .Properties = LADSPA_PROPERTY_HARD_RT_CAPABLE,
    .Name = "Sounar upmix (stereo to one speaker of a room layout)",
    .Maker = "Sounar",
    .Copyright = "MIT",
    .PortCount = NPORTS,
    .PortDescriptors = port_desc,
    .PortNames = port_names,
    .PortRangeHints = port_hints,
    .instantiate = instantiate,
    .connect_port = connect_port,
    .activate = activate,
    .run = run,
    .cleanup = cleanup,
};

__attribute__((visibility("default")))
const LADSPA_Descriptor *ladspa_descriptor(unsigned long index) {
    return index == 0 ? &descriptor : NULL;
}
