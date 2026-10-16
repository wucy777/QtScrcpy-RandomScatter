// Standalone validation of the scatter (random click spread) math.
// Mirrors the implementation in inputconvertgame.cpp::scatterPos.
//
// IMPORTANT: keep this file ASCII-only. MSVC reads source in the local
// codepage (936 here) and non-ASCII comments have broken this build before.
//
// The interesting part is the tail of the file: a chi-square test that
// proves the *uniform* mode is statistically detectable and the *gauss*
// mode is not -- i.e. gauss actually looks like a human finger.
#define _USE_MATH_DEFINES
#include <cmath>
#include <cstdio>
#include <random>
#include <algorithm>
#include <vector>

struct RandomPos
{
    enum Shape { RS_NONE = 0, RS_CIRCLE, RS_RECT };
    enum Dist { RD_UNIFORM = 0, RD_GAUSS };

    Shape shape = RS_NONE;
    double p1x = 0, p1y = 0, p2x = 0, p2y = 0;
    Dist dist = RD_UNIFORM;
    double sigma = 0.40;
    double outlier = 0.0;

    bool isValid() const { return RS_NONE != shape; }
};

struct Pt { double x, y; };

static std::mt19937_64 g_rng(12345);
static std::uniform_real_distribution<double> g_U(0.0, 1.0);

static double nextGauss()
{
    double u1 = g_U(g_rng);
    const double u2 = g_U(g_rng);
    if (u1 < 1e-12) u1 = 1e-12;
    return std::sqrt(-2.0 * std::log(u1)) * std::cos(2.0 * M_PI * u2);
}

static Pt scatterPos(const Pt &pos, const RandomPos &rp, int W, int H)
{
    if (!rp.isValid() || W <= 0 || H <= 0) return pos;

    const double frameW = W, frameH = H;

    bool forceUniform = false;
    if (rp.outlier > 0.0 && g_U(g_rng) < rp.outlier) forceUniform = true;
    const bool useGauss = (rp.dist == RandomPos::RD_GAUSS) && !forceUniform;

    if (rp.shape == RandomPos::RS_RECT) {
        double x1 = std::min(rp.p1x, rp.p2x), x2 = std::max(rp.p1x, rp.p2x);
        double y1 = std::min(rp.p1y, rp.p2y), y2 = std::max(rp.p1y, rp.p2y);
        x1 = std::max(0.0, std::min(x1, 1.0)); x2 = std::max(0.0, std::min(x2, 1.0));
        y1 = std::max(0.0, std::min(y1, 1.0)); y2 = std::max(0.0, std::min(y2, 1.0));

        if (!useGauss) {
            return { x1 + (x2 - x1) * g_U(g_rng), y1 + (y2 - y1) * g_U(g_rng) };
        }

        const double cx = (x1 + x2) * 0.5, cy = (y1 + y2) * 0.5;
        const double sx = std::max((x2 - x1) * 0.5 * rp.sigma, 1e-9);
        const double sy = std::max((y2 - y1) * 0.5 * rp.sigma, 1e-9);
        double gx = cx, gy = cy;
        for (int i = 0; i < 24; i++) {
            gx = cx + sx * nextGauss();
            gy = cy + sy * nextGauss();
            if (gx >= x1 && gx <= x2 && gy >= y1 && gy <= y2) return { gx, gy };
        }
        return { std::max(x1, std::min(gx, x2)), std::max(y1, std::min(gy, y2)) };
    }

    const double cpx = rp.p1x * frameW, cpy = rp.p1y * frameH;
    const double epx = rp.p2x * frameW, epy = rp.p2y * frameH;
    const double R = std::sqrt((epx - cpx) * (epx - cpx) + (epy - cpy) * (epy - cpy));
    if (R < 1.0) return { rp.p1x, rp.p1y };

    double px = cpx, py = cpy;

    if (!useGauss) {
        const double r = R * std::sqrt(g_U(g_rng));
        const double t = 2.0 * M_PI * g_U(g_rng);
        px = cpx + r * std::cos(t);
        py = cpy + r * std::sin(t);
    } else {
        const double s = std::max(R * rp.sigma, 1e-6);
        bool accepted = false;
        for (int i = 0; i < 24; i++) {
            const double dx = s * nextGauss();
            const double dy = s * nextGauss();
            if (dx * dx + dy * dy <= R * R) {
                px = cpx + dx; py = cpy + dy; accepted = true; break;
            }
        }
        if (!accepted) {
            double dx = px - cpx, dy = py - cpy;
            double d = std::sqrt(dx * dx + dy * dy);
            if (d < 1e-6) { const double t = 2.0 * M_PI * g_U(g_rng); dx = std::cos(t); dy = std::sin(t); d = 1.0; }
            px = cpx + dx / d * R;
            py = cpy + dy / d * R;
        }
    }

    px = std::max(0.0, std::min(px, frameW));
    py = std::max(0.0, std::min(py, frameH));
    return { px / frameW, py / frameH };
}

static int failures = 0;
static void check(bool ok, const char *what)
{
    printf("%s  %s\n", ok ? "[ OK ]" : "[FAIL]", what);
    if (!ok) failures++;
}

// Chi-square critical values, upper tail.
static const double CHI2_7DF_P001 = 24.322;    // 8 bins, df=7
static const double CHI2_15DF_P001 = 37.697;   // 4x4 grid, df=15

// Count samples per equal-AREA concentric ring.
// For a uniform disc the expected count per ring is proportional to its area,
// which is what makes the uniformity test so sharp.
static std::vector<double> ringCounts(const RandomPos &rp, int W, int H, int rings, int N)
{
    std::vector<double> c(rings, 0.0);
    const double cpx = rp.p1x * W, cpy = rp.p1y * H;
    const double R = std::sqrt((rp.p2x * W - cpx) * (rp.p2x * W - cpx) +
                               (rp.p2y * H - cpy) * (rp.p2y * H - cpy));
    for (int i = 0; i < N; i++) {
        Pt q = scatterPos({ rp.p1x, rp.p1y }, rp, W, H);
        const double dx = q.x * W - cpx, dy = q.y * H - cpy;
        const double d = std::sqrt(dx * dx + dy * dy) / R;
        int b = static_cast<int>(d * rings);
        if (b < 0) b = 0;
        if (b >= rings) b = rings - 1;
        c[b] += 1.0;
    }
    return c;
}

static double chi2Rings(const std::vector<double> &obs, int rings, int N)
{
    double chi2 = 0.0;
    for (int i = 0; i < rings; i++) {
        const double area = (double)((i + 1) * (i + 1) - i * i) / (double)(rings * rings);
        const double e = area * N;
        chi2 += (obs[i] - e) * (obs[i] - e) / e;
    }
    return chi2;
}

int main()
{
    const int W = 2670, H = 1200;   // landscape phone frame

    // 1) no randomPos -> identical fixed point (upstream compatibility)
    {
        RandomPos rp;
        Pt q = scatterPos({ 0.5, 0.5 }, rp, W, H);
        check(q.x == 0.5 && q.y == 0.5, "no randomPos => fixed point unchanged (4.2.1 compatible)");
    }

    // 2) circle: two points (centre + a point on the circumference), radius in pixels
    {
        RandomPos rp;
        rp.shape = RandomPos::RS_CIRCLE;
        rp.p1x = 0.90; rp.p1y = 0.70;
        rp.p2x = 0.90 + 30.0 / W; rp.p2y = 0.70;   // 30 px radius
        const double expectR = 30.0;
        double maxD = 0;
        bool inside = true;
        for (int i = 0; i < 200000; i++) {
            Pt q = scatterPos({ rp.p1x, rp.p1y }, rp, W, H);
            double dx = q.x * W - rp.p1x * W;
            double dy = q.y * H - rp.p1y * H;
            double d = std::sqrt(dx * dx + dy * dy);
            maxD = std::max(maxD, d);
            if (d > expectR + 0.51) inside = false;
        }
        check(inside, "circle: every sample stays inside the pixel radius");
        printf("        max radius measured = %.3f px (limit %.1f px)\n", maxD, expectR);
    }

    // 3) the circle must be a real screen circle: y offset compressed by W/H
    {
        RandomPos rp;
        rp.shape = RandomPos::RS_CIRCLE;
        rp.p1x = 0.5; rp.p1y = 0.5;
        rp.p2x = 0.5 + 60.0 / W; rp.p2y = 0.5;     // 60 px radius
        double maxDx = 0, maxDy = 0;
        for (int i = 0; i < 200000; i++) {
            Pt q = scatterPos({ 0.5, 0.5 }, rp, W, H);
            maxDx = std::max(maxDx, std::fabs(q.x - 0.5));
            maxDy = std::max(maxDy, std::fabs(q.y - 0.5));
        }
        double ratio = maxDy / maxDx;
        double expect = static_cast<double>(W) / H;
        check(std::fabs(ratio - expect) < expect * 0.02,
              "circle is a true screen circle (not an ellipse in normalised space)");
        printf("        dy/dx = %.3f, expected W/H = %.3f\n", ratio, expect);
    }

    // 4) rect: two diagonal points, uniform, in bounds
    {
        RandomPos rp;
        rp.shape = RandomPos::RS_RECT;
        rp.p1x = 0.80; rp.p1y = 0.60;
        rp.p2x = 0.88; rp.p2y = 0.68;
        bool inside = true;
        double sx = 0, sy = 0;
        const int N = 200000;
        for (int i = 0; i < N; i++) {
            Pt q = scatterPos({ 0.84, 0.64 }, rp, W, H);
            if (q.x < rp.p1x - 1e-9 || q.x > rp.p2x + 1e-9) inside = false;
            if (q.y < rp.p1y - 1e-9 || q.y > rp.p2y + 1e-9) inside = false;
            sx += q.x; sy += q.y;
        }
        check(inside, "rect: every sample stays inside the two diagonal corners");
        check(std::fabs(sx / N - 0.84) < 0.001 && std::fabs(sy / N - 0.64) < 0.001,
              "rect: uniform distribution (mean converges to centre)");
    }

    // 5) reversed diagonal order must also work
    {
        RandomPos rp;
        rp.shape = RandomPos::RS_RECT;
        rp.p1x = 0.88; rp.p1y = 0.68;
        rp.p2x = 0.80; rp.p2y = 0.60;
        bool inside = true;
        for (int i = 0; i < 50000; i++) {
            Pt q = scatterPos({ 0.84, 0.64 }, rp, W, H);
            if (q.x < 0.80 - 1e-9 || q.x > 0.88 + 1e-9) inside = false;
            if (q.y < 0.60 - 1e-9 || q.y > 0.68 + 1e-9) inside = false;
        }
        check(inside, "rect: reversed diagonal order works");
    }

    // 6) circle touching the screen edge -> samples clamped into the screen
    {
        RandomPos rp;
        rp.shape = RandomPos::RS_CIRCLE;
        rp.p1x = 0.995; rp.p1y = 0.02;
        rp.p2x = 0.995 - 40.0 / W; rp.p2y = 0.02;
        bool inScreen = true;
        for (int i = 0; i < 100000; i++) {
            Pt q = scatterPos({ rp.p1x, rp.p1y }, rp, W, H);
            if (q.x < 0 || q.x > 1 || q.y < 0 || q.y > 1) inScreen = false;
        }
        check(inScreen, "circle near edge: samples clamped inside the screen");
    }

    // 7) degenerate radius -> falls back to the centre
    {
        RandomPos rp;
        rp.shape = RandomPos::RS_CIRCLE;
        rp.p1x = 0.3; rp.p1y = 0.4;
        rp.p2x = 0.3; rp.p2y = 0.4;
        Pt q = scatterPos({ 0.3, 0.4 }, rp, W, H);
        check(q.x == 0.3 && q.y == 0.4, "degenerate radius => returns the centre");
    }

    // 8) consecutive clicks must differ (the whole point of the feature)
    {
        RandomPos rp;
        rp.shape = RandomPos::RS_CIRCLE;
        rp.p1x = 0.9; rp.p1y = 0.7;
        rp.p2x = 0.9 + 20.0 / W; rp.p2y = 0.7;
        Pt a = scatterPos({ 0.9, 0.7 }, rp, W, H);
        int diff = 0;
        for (int i = 0; i < 50; i++) {
            Pt b = scatterPos({ 0.9, 0.7 }, rp, W, H);
            if (b.x != a.x || b.y != a.y) diff++;
        }
        check(diff == 50, "consecutive clicks land on different points (anti single-point)");
    }

    // ---------------------------------------------------------------
    // Statistical tests: uniform is detectable, gauss is not.
    // This is the whole reason RD_GAUSS exists.
    // ---------------------------------------------------------------
    const int RINGS = 8;
    const int N = 400000;

    RandomPos uni;
    uni.shape = RandomPos::RS_CIRCLE;
    uni.p1x = 0.5; uni.p1y = 0.5;
    uni.p2x = 0.5 + 60.0 / W; uni.p2y = 0.5;
    uni.dist = RandomPos::RD_UNIFORM;

    RandomPos gau = uni;
    gau.dist = RandomPos::RD_GAUSS;
    gau.sigma = 0.40;

    // 9) uniform: equal-area rings hold ~equal share
    std::vector<double> cu = ringCounts(uni, W, H, RINGS, N);
    double chiU = chi2Rings(cu, RINGS, N);
    printf("        uniform ring shares:");
    for (int i = 0; i < RINGS; i++) printf(" %.3f", cu[i] / N);
    printf("\n        uniform chi2 = %.2f (df=%d, crit=%.2f)\n", chiU, RINGS - 1, CHI2_7DF_P001);
    check(chiU < CHI2_7DF_P001, "uniform mode: chi-square does NOT reject uniformity (as expected)");

    // 10) gauss: strongly centre-biased, chi-square blows up
    std::vector<double> cg = ringCounts(gau, W, H, RINGS, N);
    double chiG = chi2Rings(cg, RINGS, N);
    printf("        gauss   ring shares:");
    for (int i = 0; i < RINGS; i++) printf(" %.3f", cg[i] / N);
    printf("\n        gauss   chi2 = %.2f\n", chiG);
    check(chiG > 100.0 * CHI2_7DF_P001,
          "gauss mode: chi-square REJECTS uniformity overwhelmingly (human-like)");

    // 11) gauss must be inner-heavy: innermost bin denser than the (largest) outermost
    check(cg[0] / N > 2.0 * cu[0] / N, "gauss: innermost ring is far denser than uniform's");
    check(cg[0] > cg[RINGS - 1], "gauss: centre ring holds more hits than the rim ring");

    // 12) the modal ring for gauss is an inner ring, not the widest outer one
    {
        int argmax = 0;
        for (int i = 1; i < RINGS; i++) if (cg[i] > cg[argmax]) argmax = i;
        check(argmax <= RINGS / 2, "gauss: modal ring is near the centre (finger aims at the middle)");
        printf("        gauss modal ring = %d of %d\n", argmax, RINGS - 1);
    }

    // 13) smaller sigma => tighter cluster around the centre
    {
        RandomPos tight = gau;
        tight.sigma = 0.15;
        double sumT = 0, sumW = 0;
        for (int i = 0; i < 200000; i++) {
            Pt q = scatterPos({ gau.p1x, gau.p1y }, tight, W, H);
            double dx = q.x * W - gau.p1x * W, dy = q.y * H - gau.p1y * H;
            sumT += std::sqrt(dx * dx + dy * dy);
            Pt q2 = scatterPos({ gau.p1x, gau.p1y }, gau, W, H);
            double dx2 = q2.x * W - gau.p1x * W, dy2 = q2.y * H - gau.p1y * H;
            sumW += std::sqrt(dx2 * dx2 + dy2 * dy2);
        }
        printf("        mean radius: sigma=0.15 -> %.2f px, sigma=0.40 -> %.2f px\n",
               sumT / 200000, sumW / 200000);
        check(sumT < sumW * 0.6, "gauss: smaller sigma concentrates closer to the centre");
    }

    // 14) gauss samples still respect the region (no escape)
    {
        bool inside = true;
        double maxD = 0;
        const double R = 60.0;
        for (int i = 0; i < 200000; i++) {
            Pt q = scatterPos({ gau.p1x, gau.p1y }, gau, W, H);
            double dx = q.x * W - gau.p1x * W, dy = q.y * H - gau.p1y * H;
            double d = std::sqrt(dx * dx + dy * dy);
            maxD = std::max(maxD, d);
            if (d > R + 0.51) inside = false;
        }
        check(inside, "gauss circle: every sample still stays inside the pixel radius");
        printf("        gauss max radius = %.3f px (limit %.1f)\n", maxD, R);
    }

    // 15) gauss stays a true screen circle (isotropic in pixel space)
    {
        double maxDx = 0, maxDy = 0;
        for (int i = 0; i < 200000; i++) {
            Pt q = scatterPos({ gau.p1x, gau.p1y }, gau, W, H);
            maxDx = std::max(maxDx, std::fabs(q.x - gau.p1x));
            maxDy = std::max(maxDy, std::fabs(q.y - gau.p1y));
        }
        double ratio = maxDy / maxDx;
        double expect = static_cast<double>(W) / H;
        check(std::fabs(ratio - expect) < expect * 0.05,
              "gauss circle is also a true screen circle (pixel-space isotropic)");
        printf("        gauss dy/dx = %.3f, expected %.3f\n", ratio, expect);
    }

    // 16) rect gauss: centre-dense on both axes, still inside
    {
        RandomPos rg;
        rg.shape = RandomPos::RS_RECT;
        rg.p1x = 0.80; rg.p1y = 0.60;
        rg.p2x = 0.88; rg.p2y = 0.68;
        rg.dist = RandomPos::RD_GAUSS;
        rg.sigma = 0.40;
        const int G = 4;
        std::vector<double> grid(G * G, 0.0);
        bool inside = true;
        for (int i = 0; i < 200000; i++) {
            Pt q = scatterPos({ 0.84, 0.64 }, rg, W, H);
            if (q.x < rg.p1x - 1e-9 || q.x > rg.p2x + 1e-9) inside = false;
            if (q.y < rg.p1y - 1e-9 || q.y > rg.p2y + 1e-9) inside = false;
            int bx = (int)((q.x - rg.p1x) / (rg.p2x - rg.p1x) * G);
            int by = (int)((q.y - rg.p1y) / (rg.p2y - rg.p1y) * G);
            bx = std::max(0, std::min(bx, G - 1));
            by = std::max(0, std::min(by, G - 1));
            grid[by * G + bx] += 1.0;
        }
        check(inside, "gauss rect: every sample stays inside the two diagonal corners");
        const double e = 200000.0 / (G * G);
        double chi = 0;
        for (int i = 0; i < G * G; i++) chi += (grid[i] - e) * (grid[i] - e) / e;
        printf("        gauss rect chi2 = %.2f (df=%d, crit=%.2f)\n", chi, G * G - 1, CHI2_15DF_P001);
        check(chi > CHI2_15DF_P001, "gauss rect: cell counts are NOT flat (uniform would be flat)");
    }

    // 17) outlier = 1.0 degenerates gauss back to uniform
    {
        RandomPos oo = gau;
        oo.outlier = 1.0;
        std::vector<double> co = ringCounts(oo, W, H, RINGS, N);
        double chiO = chi2Rings(co, RINGS, N);
        printf("        outlier=1.0 chi2 = %.2f\n", chiO);
        check(chiO < CHI2_7DF_P001, "outlier=1.0 makes gauss behave as uniform (expected fallback)");
    }

    // 18) outlier is just a small perturbation, it must not flatten the gauss mode
    {
        RandomPos oo = gau;
        oo.outlier = 0.15;
        std::vector<double> co = ringCounts(oo, W, H, RINGS, N);
        double chiO = chi2Rings(co, RINGS, N);
        printf("        outlier=0.15 chi2 = %.2f, inner ring share = %.3f\n", chiO, co[0] / N);
        check(chiO > 10.0 * CHI2_7DF_P001, "outlier=0.15 keeps the heavy centre bias");
        check(oo.outlier > 0.0, "outlier heavy-tail mode is exercised");
    }

    printf("\n%s (failures=%d)\n", failures == 0 ? "ALL TESTS PASSED" : "TESTS FAILED", failures);
    return failures == 0 ? 0 : 1;
}
