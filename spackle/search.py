"""Best-known circuit search (plan.md: "best-known sizes come from our own
exhaustive or random search, not from published designs").

Simulated annealing over wire layouts. The cost is

    PENALTY * (truth-table errors) + cell count

so a run first finds any passing design and then shrinks it. Every passing
design seen at a local minimum is kept: the smallest gives c_min for T3, and
the rest form the known-good circuit pool that T2 breaks.

Usage:  python -m spackle.search [--geoms XOR/a ...] [--seconds 60] [--jobs 4]
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import random
import shutil
import subprocess
import tempfile
import time
from multiprocessing import Pool
from pathlib import Path

from .circuit import evaluate, evaluate_mask, harnesses
from .specs import GEOMETRIES, Geometry

DATA = Path(__file__).resolve().parent.parent / "data" / "best_known.json"
PENALTY = 12
POOL_PER_GEOM = 40


def _cost_fn(geom: Geometry):
    truth_h, _, expected = harnesses(geom)

    def errors(K: int) -> int:
        pulses, quiet = truth_h.run(K)
        e = 0
        for p, want, q in zip(pulses, expected, quiet):
            for a, b in zip(p, want):
                e += abs(min(a, 2) - b)
            e += not q
        return e

    return errors


def _initial(geom: Geometry, rng: random.Random) -> int:
    """Ports plus a jittered path from every input towards every output."""
    lay = harnesses(geom)[0].lay
    K = harnesses(geom)[0].port_mask
    for r0 in geom.in_rows:
        for r1 in geom.out_rows:
            r = r0
            for c in range(1, geom.cols - 1):
                remaining = geom.cols - 1 - c
                if abs(r1 - r) >= remaining or (r != r1 and rng.random() < 0.5):
                    r += (r1 > r) - (r1 < r)
                K |= lay.bit(r, c)
    return K


def anneal(geom: Geometry, seed: int, seconds: float, start: int | None = None):
    """Returns list of (cells, K) passing local minima found, best first."""
    rng = random.Random(seed)
    truth_h = harnesses(geom)[0]
    lay = truth_h.lay
    ports = truth_h.port_mask
    free = [lay.bit(r, c) for r in range(geom.rows) for c in range(geom.cols)
            if not lay.bit(r, c) & ports]
    W = lay.W
    errors = _cost_fn(geom)
    found: dict[int, int] = {}
    deadline = time.time() + seconds
    restart = 0
    while time.time() < deadline:
        K = start if (start is not None and restart % 2 == 0) else _initial(geom, rng)
        restart += 1
        err = errors(K)
        cells = K.bit_count()
        cost = PENALTY * err + cells
        steps = 60000
        t_hi, t_lo = 2.5, 0.25
        best_pass = None
        for i in range(steps):
            if i % 512 == 0 and time.time() > deadline:
                break
            temp = t_hi * (t_lo / t_hi) ** (i / steps)
            move = rng.random()
            K2 = K
            if move < 0.4:      # remove a wire
                b = rng.choice(free)
                if not K & b:
                    continue
                K2 = K ^ b
            else:               # add next to existing wire (and maybe drop one: a "move")
                b = rng.choice(free)
                if K & b:
                    continue
                near = (b << 1 | b >> 1 | b << W | b >> W | b << (W + 1)
                        | b >> (W + 1) | b << (W - 1) | b >> (W - 1))
                if not near & K:
                    continue
                K2 = K | b
                if move > 0.7:
                    d = rng.choice(free)
                    if K2 & d and d != b:
                        K2 ^= d
            err2 = errors(K2)
            cost2 = PENALTY * err2 + K2.bit_count()
            if cost2 <= cost or rng.random() < math.exp((cost - cost2) / temp):
                K, err, cost = K2, err2, cost2
                if err == 0 and (best_pass is None or K.bit_count() < best_pass.bit_count()):
                    best_pass = K
        if best_pass is not None:
            best_pass = _greedy_shrink(best_pass, free, errors)
            found[best_pass] = best_pass.bit_count()
    return sorted(((n, K) for K, n in found.items()))


def _greedy_shrink(K: int, free, errors) -> int:
    """Delete cells while the design still passes (to a 1-deletion minimum)."""
    changed = True
    while changed:
        changed = False
        for b in free:
            if K & b and errors(K ^ b) == 0:
                K ^= b
                changed = True
    return K


def _kernel():
    """Compile (once) and load the C search kernel; None if no compiler."""
    src = Path(__file__).with_name("_anneal.c")
    cache = Path(tempfile.gettempdir()) / f"spackle_anneal_{hashlib.sha1(src.read_bytes()).hexdigest()[:12]}.so"
    if not cache.exists():
        cc = shutil.which("cc") or shutil.which("gcc")
        if not cc:
            return None
        tmp = cache.with_suffix(f".{os.getpid()}.tmp")
        subprocess.run([cc, "-O2", "-shared", "-fPIC", "-o", str(tmp), str(src), "-lm"], check=True)
        os.replace(tmp, cache)
    return ctypes.CDLL(str(cache))


def anneal_c(lib, geom: Geometry, seed: int, seconds: float, start: int | None = None,
             steps: int = 400000, penalty: float = PENALTY, max_out: int = 64,
             t_hi: float = 2.5, t_lo: float = 0.25):
    """Same search as ``anneal`` in C (roughly 50x faster)."""
    truth_h, _, expected = harnesses(geom)
    lay = truth_h.lay
    n_in, n_out, n_scen = len(geom.in_rows), len(geom.out_rows), len(truth_h.scenarios)
    ints = lambda xs: (ctypes.c_int * len(xs))(*xs)
    inject = ints([-1 if t is None else t for s in truth_h.scenarios for t in s])
    exp = ints([e for row in expected for e in row])
    size = geom.rows * geom.cols
    out = (ctypes.c_ubyte * (max_out * size))()
    start_buf = None
    if start is not None:
        start_buf = (ctypes.c_ubyte * size)(*[
            1 if start & lay.bit(r, c) else 0
            for r in range(geom.rows) for c in range(geom.cols)])
    lib.anneal.restype = ctypes.c_int
    n = lib.anneal(geom.rows, geom.cols, n_in, ints(geom.in_rows), n_out, ints(geom.out_rows),
                   n_scen, inject, exp, ctypes.c_uint64(seed + 1), ctypes.c_double(seconds),
                   start_buf, out, max_out, steps, ctypes.c_double(penalty),
                   ctypes.c_double(t_hi), ctypes.c_double(t_lo))
    found = {}
    for i in range(max(n, 0)):
        K = 0
        for j in range(size):
            if out[i * size + j]:
                K |= lay.bit(j // geom.cols, j % geom.cols)
        found[K] = K.bit_count()
    return sorted((c, K) for K, c in found.items())


def _worker(args):
    key, seed, seconds, start, opts = args
    geom = GEOMETRIES[key]
    lib = _kernel()
    if lib is None:
        return key, anneal(geom, seed, seconds, start)
    return key, anneal_c(lib, geom, seed, seconds, start, **opts)


def load() -> dict:
    if DATA.exists():
        return json.loads(DATA.read_text())
    return {}


def save(db: dict) -> None:
    DATA.parent.mkdir(exist_ok=True)
    DATA.write_text(json.dumps(db, indent=1, sort_keys=True) + "\n")


def merge(db: dict, key: str, designs, provenance: str) -> None:
    """Merge passing designs into the database entry for ``key``."""
    geom = GEOMETRIES[key]
    lay = harnesses(geom)[0].lay
    entry = db.setdefault(key, {"designs": []})
    seen = {tuple(d["grid"]) for d in entry["designs"]}
    for _, K in designs:
        grid = lay.unpack(K)
        if tuple(grid) in seen:
            continue
        rep = evaluate(grid, geom)
        assert rep.valid and rep.passed, "search produced a non-passing design"
        seen.add(tuple(grid))
        entry["designs"].append({"grid": grid, "cells": rep.cells,
                                 "robustness": round(rep.robustness, 4),
                                 "provenance": provenance})
    # Keep the smallest designs, but always retain the smallest fully robust one.
    ds = sorted(entry["designs"], key=lambda d: (d["cells"], -d["robustness"], d["grid"]))
    keep = ds[:POOL_PER_GEOM]
    robust = [d for d in ds if d["robustness"] == 1.0]
    if robust and robust[0] not in keep:
        keep.append(robust[0])
    entry["designs"] = keep
    entry["c_min"] = keep[0]["cells"] if keep else None


def diversify(db: dict, key: str, seed: int = 0, slack: float = 1.25, stride: int = 25) -> None:
    """Fill the pool for ``key`` with neutral variants of its designs.

    A random walk that adds, removes or moves wire cells and keeps only layouts
    that still pass and stay within ``slack`` x c_min cells. Every ``stride``
    accepted moves the current layout is shrunk and stored. This gives T2 many
    distinct known-good circuits where annealing alone finds few.
    """
    geom = GEOMETRIES[key]
    truth_h = harnesses(geom)[0]
    lay, ports, W = truth_h.lay, truth_h.port_mask, truth_h.lay.W
    free = [lay.bit(r, c) for r in range(geom.rows) for c in range(geom.cols)
            if not lay.bit(r, c) & ports]
    errors = _cost_fn(geom)
    rng = random.Random(seed)
    entry = db[key]
    cap = int(entry["c_min"] * slack)
    tries = 0
    while len(entry["designs"]) < POOL_PER_GEOM and tries < 40:
        tries += 1
        K = lay.pack(rng.choice(entry["designs"])["grid"])[0]
        accepted = attempts = 0
        while accepted < stride and attempts < 20000:
            attempts += 1
            b = rng.choice(free)
            if K & b:
                K2 = K ^ b
            else:
                near = (b << 1 | b >> 1 | b << W | b >> W | b << (W + 1)
                        | b >> (W + 1) | b << (W - 1) | b >> (W - 1))
                if not near & K:
                    continue
                K2 = K | b
            if K2.bit_count() <= cap and errors(K2) == 0:
                K = K2
                accepted += 1
        K = _greedy_shrink(K, rng.sample(free, len(free)), errors)
        merge(db, key, [(K.bit_count(), K)], f"neutral walk seed={seed}")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--geoms", nargs="*", default=sorted(GEOMETRIES))
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--steps", type=int, default=400000, help="annealing steps per restart")
    ap.add_argument("--penalty", type=float, default=PENALTY)
    ap.add_argument("--t-hi", type=float, default=2.5)
    ap.add_argument("--t-lo", type=float, default=0.25)
    ap.add_argument("--diversify", action="store_true",
                    help="only fill pools with neutral variants of existing designs")
    args = ap.parse_args(argv)
    db = load()
    if args.diversify:
        for key in args.geoms:
            diversify(db, key, args.seed)
            save(db)
            print(f"{key}: c_min={db[key]['c_min']} pool={len(db[key]['designs'])}", flush=True)
        return
    for key in args.geoms:
        geom = GEOMETRIES[key]
        lay = harnesses(geom)[0].lay
        prev = db.get(key, {}).get("designs", [])
        start = lay.pack(prev[0]["grid"])[0] if prev else None
        opts = {"steps": args.steps, "penalty": args.penalty, "t_hi": args.t_hi, "t_lo": args.t_lo}
        _kernel()  # compile before forking
        jobs = [(key, args.seed * 1000 + j, args.seconds, start, opts) for j in range(args.jobs)]
        with Pool(args.jobs) as pool:
            results = pool.map(_worker, jobs)
        for (_, designs), (_, seed, *_rest) in zip(results, jobs):
            merge(db, key, designs, f"anneal seed={seed} {args.seconds:g}s")
        save(db)
        e = db.get(key, {})
        print(f"{key}: c_min={e.get('c_min')} pool={len(e.get('designs', []))}", flush=True)
        if e.get("designs"):
            print("\n".join("  |" + row + "|" for row in e["designs"][0]["grid"]), flush=True)


if __name__ == "__main__":
    main()
