"""Team planner: turn this week's priority blocks into non-overlapping inspection sites.

Input is gold_block_priority (one row per HDB block with SVY21 x/y, homes and a weight). Each team
gets one site: an anchor block plus every priority block within `radius` metres of it. Sites are
picked greedily, each covering the most weight not already covered (the prototype's d8.py), so no
two teams inspect the same block.

The comparison is the simple plan: send each team to the highest-weight block not within
2 x radius of another team's, i.e. the top blocks spread out so sites don't overlap. (The
prototype's naive plan, top blocks with overlaps allowed, can put all teams on one street,
which flatters the planner.) Both are scored by the priority homes they reach.
"""

import numpy as np
import pandas as pd


def _within(x, y, radius, chunk=1000):
    """For each block, the indices of blocks within radius metres (itself included)."""
    out = []
    r2 = radius * radius
    for i in range(0, len(x), chunk):
        dx = x[i:i + chunk, None] - x[None, :]
        dy = y[i:i + chunk, None] - y[None, :]
        out += [np.flatnonzero(row) for row in (dx * dx + dy * dy <= r2)]
    return out


def plan(blocks, teams=6, radius=300.0):
    """Returns (sites, assigned_blocks, summary).

    sites: one row per team (anchor block, area, blocks, homes, how many near a cluster, homes in Wolbachia).
    assigned_blocks: every block a team covers, with its team number.
    summary: priority homes and weight reached by the plan and by the simple plan.
    """
    b = blocks.reset_index(drop=True)
    x, y, w = b.x.to_numpy(float), b.y.to_numpy(float), b.weight.to_numpy(float)
    homes = b.homes.to_numpy(float)
    near = _within(x, y, radius)

    covered = np.zeros(len(b), bool)
    team_of = np.zeros(len(b), int)
    sites = []
    for t in range(1, teams + 1):
        gain = np.array([w[n[~covered[n]]].sum() for n in near])
        j = int(np.argmax(gain))
        if gain[j] <= 0:
            break
        sel = near[j][~covered[near[j]]]
        covered[sel] = True
        team_of[sel] = t
        a = b.iloc[j]
        sites.append({
            "team": t,
            "anchor": f"Blk {a.blk_no} {str(a.street).title()}",
            "area": str(a.planning_area).title(),
            "lon": float(a.lon), "lat": float(a.lat),
            "blocks": int(len(sel)),
            "homes": int(homes[sel].sum()),
            "blocks_near_cluster": int(b.near_cluster.to_numpy()[sel].sum()),
            "homes_in_wolbachia": int(homes[sel][b.in_wolbachia.to_numpy()[sel]].sum()),
            "weight": float(w[sel].sum()),
        })

    simple, anchors = np.zeros(len(b), bool), []
    for j in np.argsort(-w):
        if len(anchors) == teams:
            break
        if all((x[j] - x[a]) ** 2 + (y[j] - y[a]) ** 2 > (2 * radius) ** 2 for a in anchors):
            anchors.append(j)
            simple[near[j]] = True

    assigned = b[covered].assign(team=team_of[covered])
    summary = pd.DataFrame([{
        "teams": teams, "radius_m": radius,
        "priority_blocks": len(b), "priority_homes": int(homes.sum()),
        "plan_homes": int(homes[covered].sum()), "simple_homes": int(homes[simple].sum()),
        "plan_weight": float(w[covered].sum()), "simple_weight": float(w[simple].sum()),
    }])
    summary["homes_ratio"] = summary.plan_homes / summary.simple_homes.where(summary.simple_homes > 0)
    return pd.DataFrame(sites), assigned, summary
