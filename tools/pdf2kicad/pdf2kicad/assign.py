"""Minimum-cost assignment (Hungarian algorithm, O(n^3)) - small, dependency-free replacement for scipy."""
import numpy as np


def linear_sum_assignment(cost):
    """cost: n x m matrix. Returns (row_indices, col_indices) of a minimum-cost matching of min(n, m) pairs."""
    c = np.asarray(cost, dtype=float)
    n, m = c.shape
    transposed = n > m
    if transposed:
        c, n, m = c.T, m, n
    INF = float("inf")
    u = np.zeros(n + 1)
    v = np.zeros(m + 1)
    p = np.zeros(m + 1, dtype=int)          # p[j] = row matched to column j (1-based, 0 = none)
    way = np.zeros(m + 1, dtype=int)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = np.full(m + 1, INF)
        used = np.zeros(m + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]
            cur = c[i0 - 1] - u[i0] - v[1:]
            free = ~used[1:]
            upd = free & (cur < minv[1:])
            minv[1:][upd] = cur[upd]
            way[1:][upd] = j0
            cand = np.where(free, minv[1:], INF)
            j1 = int(cand.argmin()) + 1
            delta = cand[j1 - 1]
            u[p[used]] += delta
            v[used] -= delta
            minv[~used] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while j0:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
    rows, cols = [], []
    for j in range(1, m + 1):
        if p[j]:
            rows.append(p[j] - 1)
            cols.append(j - 1)
    rows, cols = np.array(rows), np.array(cols)
    if transposed:
        rows, cols = cols, rows
    order = np.argsort(rows)
    return rows[order], cols[order]
