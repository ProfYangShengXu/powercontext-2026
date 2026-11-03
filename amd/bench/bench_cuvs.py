#!/usr/bin/env python3
"""CPU exact kNN vs cuVS brute force —— 同一份数据、同一 top-k、同一度量。

用法:
    python bench_cuvs.py --n 500000 --d 768 --k 10 --nq 200 --out results.json

输出: {n,d,k,nq,cpu_sec,gpu_build_sec,gpu_search_sec,speedup_total,agree@k}
agree@k = GPU top-k 落在 CPU top-k 里的比例（防"快了但算错"）。
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np


def cpu_topk(base: np.ndarray, queries: np.ndarray, k: int, block: int = 1024):
    """分块算，避免 n*q 一次爆内存。"""
    out_i = np.empty((queries.shape[0], k), dtype=np.int64)
    out_d = np.empty((queries.shape[0], k), dtype=np.float32)
    bn = (base ** 2).sum(1)
    for s in range(0, queries.shape[0], block):
        q = queries[s:s + block]
        d = (q ** 2).sum(1)[:, None] + bn[None, :] - 2.0 * (q @ base.T)
        idx = np.argpartition(d, k, axis=1)[:, :k]
        rows = np.arange(d.shape[0])[:, None]
        picked = d[rows, idx]
        order = np.argsort(picked, axis=1)
        out_i[s:s + block] = idx[rows, order]
        out_d[s:s + block] = picked[rows, order]
    return out_d, out_i


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100_000)
    ap.add_argument("--d", type=int, default=128)
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--nq", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="results.json")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    base = rng.random((args.n, args.d), dtype=np.float32)
    queries = rng.random((args.nq, args.d), dtype=np.float32)

    t0 = time.perf_counter()
    _cd, ci = cpu_topk(base, queries, args.k)
    cpu_t = time.perf_counter() - t0

    res = {"n": args.n, "d": args.d, "k": args.k, "nq": args.nq,
           "cpu_sec": round(cpu_t, 3)}

    try:
        from cuvs.neighbors import brute_force
        import cupy as cp
    except Exception as e:                                    # noqa: BLE001
        res["gpu_error"] = f"{type(e).__name__}: {e}"[:300]
        print(json.dumps(res, ensure_ascii=False, indent=1))
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        return

    gbase, gq = cp.asarray(base), cp.asarray(queries)
    t0 = time.perf_counter()
    index = brute_force.build(gbase, metric="sqeuclidean")
    cp.cuda.Stream.null.synchronize()
    build_t = time.perf_counter() - t0

    t0 = time.perf_counter()
    _gd, gi = brute_force.search(index, gq, args.k)
    cp.cuda.Stream.null.synchronize()
    search_t = time.perf_counter() - t0

    gi_np = cp.asnumpy(gi)
    hit = sum(len(set(a.tolist()) & set(b.tolist())) for a, b in zip(gi_np, ci))
    total = build_t + search_t
    res.update({
        "gpu_build_sec": round(build_t, 3),
        "gpu_search_sec": round(search_t, 3),
        "speedup_total": round(cpu_t / total, 2) if total > 0 else None,
        "agree@k": round(hit / (args.nq * args.k), 4),
    })
    print(json.dumps(res, ensure_ascii=False, indent=1))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
