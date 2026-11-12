/* Search kernel for spackle.search: simulated annealing over wire layouts.
 *
 * Only the search uses this; the benchmark itself is pure Python, and every
 * design returned from here is re-verified by the Python evaluator.
 * Semantics mirror fastsim.Harness.run exactly (inject, 60 steps, pulse counts,
 * quiet at end).
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define MAXN 1024
#define MAX_STEPS 60

typedef struct {
    int rows, cols, W, N;
    int n_in, n_out, n_scen;
    int in_cell[8], out_cell[8];
    const int *inject;   /* n_scen * n_in, -1 = not driven */
    const int *expected; /* n_scen * n_out */
    int nb[8];
} Ctx;

static uint64_t rs;
static inline uint64_t rnd(void) {
    rs ^= rs << 13; rs ^= rs >> 7; rs ^= rs << 17; return rs;
}
static inline double rnd01(void) { return (rnd() >> 11) * (1.0 / 9007199254740992.0); }

static unsigned char st[MAXN];
static unsigned char cnt[MAXN];

/* returns 1 if quiet at end; pulses[] filled */
static int simulate(const Ctx *x, const unsigned char *w, const int *inj, int *pulses) {
    int heads[MAXN], tails[MAXN], touched[MAXN], nh = 0, nt = 0, last = 0;
    for (int j = 0; j < x->n_out; j++) pulses[j] = 0;
    for (int i = 0; i < x->n_in; i++) {
        if (inj[i] > last) last = inj[i];
        if (inj[i] == 0 && w[x->in_cell[i]]) { st[x->in_cell[i]] = 1; heads[nh++] = x->in_cell[i]; }
    }
    int t = 0;
    for (;;) {
        for (int j = 0; j < x->n_out; j++) if (st[x->out_cell[j]] == 1) pulses[j]++;
        if (t == MAX_STEPS) break;
        if (nh == 0 && nt == 0 && t >= last) break;
        int ntouch = 0;
        for (int a = 0; a < nh; a++)
            for (int d = 0; d < 8; d++) {
                int c = heads[a] + x->nb[d];
                if (w[c] && st[c] == 0) { if (cnt[c]++ == 0) touched[ntouch++] = c; }
            }
        for (int a = 0; a < nt; a++) if (st[tails[a]] == 2) st[tails[a]] = 0;
        for (int a = 0; a < nh; a++) { st[heads[a]] = 2; tails[a] = heads[a]; }
        nt = nh; nh = 0;
        for (int a = 0; a < ntouch; a++) {
            int c = touched[a];
            if (cnt[c] <= 2) { st[c] = 1; heads[nh++] = c; }
            cnt[c] = 0;
        }
        t++;
        for (int i = 0; i < x->n_in; i++)
            if (inj[i] == t && w[x->in_cell[i]] && st[x->in_cell[i]] != 1) {
                st[x->in_cell[i]] = 1; heads[nh++] = x->in_cell[i];
            }
    }
    int live = nh;
    for (int a = 0; a < nt; a++) if (st[tails[a]] == 2) { live++; st[tails[a]] = 0; }
    for (int a = 0; a < nh; a++) st[heads[a]] = 0;
    return live == 0;
}

static int errors(const Ctx *x, const unsigned char *w) {
    int e = 0, p[8];
    for (int k = 0; k < x->n_scen; k++) {
        int q = simulate(x, w, x->inject + k * x->n_in, p);
        for (int j = 0; j < x->n_out; j++) {
            int a = p[j] > 2 ? 2 : p[j];
            e += abs(a - x->expected[k * x->n_out + j]);
        }
        e += !q;
    }
    return e;
}

static double now(void) {
    struct timespec ts; clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec + ts.tv_nsec * 1e-9;
}

static int count(const Ctx *x, const unsigned char *w) {
    int n = 0; for (int i = 0; i < x->N; i++) n += w[i]; return n;
}

/* grids are passed unpadded (rows*cols bytes of 0/1) */
int anneal(int rows, int cols, int n_in, const int *in_rows, int n_out, const int *out_rows,
           int n_scen, const int *inject, const int *expected,
           uint64_t seed, double seconds, const unsigned char *start,
           unsigned char *out, int max_out, int steps, double penalty,
           double t_hi, double t_lo) {
    Ctx x; x.rows = rows; x.cols = cols; x.W = cols + 2; x.N = (rows + 2) * x.W;
    if (x.N > MAXN) return -1;
    x.n_in = n_in; x.n_out = n_out; x.n_scen = n_scen; x.inject = inject; x.expected = expected;
    int W = x.W;
    int nb[8] = {1, -1, W, -W, W + 1, -W - 1, W - 1, -W + 1};
    memcpy(x.nb, nb, sizeof nb);
    unsigned char port[MAXN]; memset(port, 0, sizeof port);
    for (int i = 0; i < n_in; i++) { x.in_cell[i] = (in_rows[i] + 1) * W + 1; port[x.in_cell[i]] = 1; }
    for (int j = 0; j < n_out; j++) { x.out_cell[j] = (out_rows[j] + 1) * W + cols; port[x.out_cell[j]] = 1; }
    int freec[MAXN], nfree = 0;
    for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++) {
        int i = (r + 1) * W + c + 1; if (!port[i]) freec[nfree++] = i;
    }
    rs = seed * 0x9E3779B97F4A7C15ULL + 0x1234567ULL; for (int i = 0; i < 8; i++) rnd();
    memset(st, 0, sizeof st); memset(cnt, 0, sizeof cnt);
    double deadline = now() + seconds;
    int n_found = 0, restart = 0;
    unsigned char w[MAXN], w2[MAXN], best[MAXN];
    while (now() < deadline && n_found < max_out) {
        memset(w, 0, sizeof w);
        if (start && (restart % 2 == 0)) {
            for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++)
                w[(r + 1) * W + c + 1] = start[r * cols + c];
        } else {
            for (int i = 0; i < n_in; i++) for (int j = 0; j < n_out; j++) {
                int r = in_rows[i], r1 = out_rows[j];
                for (int c = 1; c < cols - 1; c++) {
                    int rem = cols - 1 - c;
                    if (abs(r1 - r) >= rem || (r != r1 && rnd01() < 0.5)) r += (r1 > r) - (r1 < r);
                    w[(r + 1) * W + c + 1] = 1;
                }
            }
        }
        for (int i = 0; i < x.N; i++) if (port[i]) w[i] = 1;
        restart++;
        int err = errors(&x, w), cells = count(&x, w), have_best = 0, best_cells = 1 << 30;
        double cost = penalty * err + cells;
        for (int it = 0; it < steps; it++) {
            if ((it & 1023) == 0 && now() > deadline) break;
            double temp = t_hi * pow(t_lo / t_hi, (double)it / steps);
            double mv = rnd01();
            int b = freec[rnd() % nfree], d = -1;
            if (mv < 0.4) { if (!w[b]) continue; }
            else {
                if (w[b]) continue;
                int near = 0; for (int q = 0; q < 8; q++) near |= w[b + nb[q]];
                if (!near) continue;
                if (mv > 0.7) { d = freec[rnd() % nfree]; if (!w[d] || d == b) d = -1; }
            }
            w[b] ^= 1; if (d >= 0) w[d] = 0;
            int err2 = errors(&x, w);
            int cells2 = cells + (w[b] ? 1 : -1) - (d >= 0);
            double cost2 = penalty * err2 + cells2;
            if (cost2 <= cost || rnd01() < exp((cost - cost2) / temp)) {
                err = err2; cells = cells2; cost = cost2;
                if (err == 0 && cells < best_cells) { memcpy(best, w, sizeof w); best_cells = cells; have_best = 1; }
            } else { w[b] ^= 1; if (d >= 0) w[d] = 1; }
        }
        if (have_best) {
            int changed = 1;
            while (changed) {
                changed = 0;
                for (int a = 0; a < nfree; a++) {
                    int i = freec[a];
                    if (best[i]) { best[i] = 0; if (errors(&x, best) == 0) changed = 1; else best[i] = 1; }
                }
            }
            unsigned char *o = out + (size_t)n_found * rows * cols;
            for (int r = 0; r < rows; r++) for (int c = 0; c < cols; c++)
                o[r * cols + c] = best[(r + 1) * W + c + 1];
            n_found++;
        }
        (void)w2;
    }
    return n_found;
}
