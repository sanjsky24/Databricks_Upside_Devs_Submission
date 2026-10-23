"""DengueRadar models: area risk level (Low/Medium/High) and new-cluster score.

Plain pandas + scikit-learn so the same code runs in a Databricks notebook and on a laptop.
Input is the gold_area_fortnight table (one row per planning area per list date).

Honest evaluation:
  every choice (feature sets, decision thresholds) is made on the development years only, with an
  expanding-window backtest: predict 2018 from 2015-17, 2019 from 2015-18, 2020 from 2015-19 and
  2022 from 2015-20 (483 new clusters and 1,061 High outcomes, instead of the 42 new clusters
  2022-23 alone gave) -> retrain on 2015-2023 -> test ONCE on 2024-26 -> retrain on every labelled
  row for the live forecast.

The risk-level model predicts High when P(High) clears a threshold tuned on the backtest for High
F1, instead of class-weighting + most-likely class, which bought recall with precision.

Area history enters as recency-weighted averages (lhist_avg, cls_recent), not the sum-based
lhist / cls_last3: NEA lists were captured about weekly in 2015-20, about monthly in 2024-26
and every 4 days live, and a sum would mean something different in each. The old columns
stay as the "Past clusters here" and "Recent-history average" baselines.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

LMH_FEATURES = ["cls", "lrecent", "ltotal", "n", "lhist_avg", "cls_recent", "nbr", "nat", "du", "wol_dyn", "ms", "mc"]

# National monthly weather known on the list date (anomalies vs 1982-2014; gold_weather_monthly)
WEATHER = ["rain_anom_1m", "rain_anom_3m", "rain_days_anom_3m", "tmax_anom_1m", "tmax_anom_3m", "temp_anom_3m",
           "rh_anom_3m"]

# Candidate feature sets for the risk level, smallest first
LMH_SETS = {
    "clusters & history": LMH_FEATURES,
    "+ weather": LMH_FEATURES + WEATHER,
}

# Candidate feature sets for the new-cluster score (areas with no cluster today), smallest first
NEW_SETS = {
    "history": ["lhist_avg", "cls_recent"],
    "+ neighbours": ["lhist_avg", "cls_recent", "nbr"],
    "+ homes": ["lhist_avg", "cls_recent", "nbr", "du"],
    "+ Wolbachia (by release date)": ["lhist_avg", "cls_recent", "nbr", "du", "wol_dyn"],
    "+ season & national": ["lhist_avg", "cls_recent", "nbr", "du", "wol_dyn", "nat", "ms", "mc"],
    "+ weather": ["lhist_avg", "cls_recent", "nbr", "du", "wol_dyn", "nat", "ms", "mc"] + WEATHER,
}

TEST_START = "2024-01-01"
BACKTEST_YEARS = (2018, 2019, 2020, 2022)  # 2021 and 2023 have no labelled lists (thin archive years)
LEVELS = {0: "Low", 1: "Medium", 2: "High"}


def split(df):
    """(development 2015-2023, test 2024-26) labelled rows."""
    d = df[df.y.notna()].copy()
    d["y"] = d.y.astype(int)
    t = pd.to_datetime(d.snapshot_date)
    return d[t < TEST_START], d[t >= TEST_START]


def backtest(dev, fit_score):
    """Out-of-fold scores on the development years: each year predicted by a model fit on the years before it."""
    year = pd.to_datetime(dev.snapshot_date).dt.year
    parts = []
    for y in BACKTEST_YEARS:
        tr, va = dev[year < y], dev[year == y]
        parts.append(va.assign(score=fit_score(tr, va)))
    return pd.concat(parts)


def prf(y, pred):
    """Precision, recall, F1 for boolean arrays."""
    tp = int((pred & y).sum())
    pr = tp / pred.sum() if pred.sum() else float("nan")
    rc = tp / y.sum() if y.sum() else float("nan")
    return pr, rc, (2 * pr * rc / (pr + rc) if tp else 0.0)


def best_threshold(y, s):
    """Score cut-off with the best F1 (on backtest scores only)."""
    grid = np.unique(np.quantile(s, np.linspace(0.3, 0.995, 300)))
    return float(grid[int(np.argmax([prf(y, s >= t)[2] for t in grid]))])


def boot_diff(test, metric, a, b, n=2000, seed=0):
    """90% interval of metric(a) - metric(b), resampling test list dates with replacement."""
    dates = test.snapshot_date.unique()
    by = {d: g for d, g in test.groupby("snapshot_date")}
    rng = np.random.RandomState(seed)
    out = []
    for _ in range(n):
        s = pd.concat([by[d] for d in rng.choice(dates, len(dates))])
        out.append(metric(s, a) - metric(s, b))
    return tuple(np.percentile(out, [5, 95]))


# ------------------------------------------------------------------ area risk level

def fit_lmh(train, features):
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000)).fit(train[features], train.y)


def fit_lmh_gbm(train, features):
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=3, min_samples_leaf=30,
                                          random_state=0).fit(train[features], train.y)


def to_level(p, t_high, t_any):
    """High if P(High) >= t_high, else Medium if P(Medium or High) >= t_any, else Low."""
    return np.where(p[:, 2] >= t_high, 2, np.where(p[:, 1] + p[:, 2] >= t_any, 1, 0))


def choose_lmh(dev):
    """Pick the risk-level feature set and both thresholds on the backtest (ties go to the smaller set)."""
    rows = []
    for name, feats in LMH_SETS.items():
        o = backtest(dev, lambda tr, va, f=feats: list(fit_lmh(tr, f).predict_proba(va[f])))
        p = np.vstack(o.score.values)
        hi = o.y.values == 2
        t_high = best_threshold(hi, p[:, 2])
        rest = p[:, 2] < t_high   # Medium is decided among areas not called High
        t_any = best_threshold(o.y.values[rest] >= 1, (p[:, 1] + p[:, 2])[rest])
        pr, rc, f1 = prf(hi, p[:, 2] >= t_high)
        rows.append({"name": name, "high_precision": pr, "high_recall": rc, "high_f1": f1,
                     "macro_f1": f1_score(o.y, to_level(p, t_high, t_any), average="macro"),
                     "t_high": t_high, "t_any": t_any})
    v = pd.DataFrame(rows)
    best = v.high_f1.round(3).max()
    choice = v[v.high_f1.round(3) == best].iloc[0]
    sameasnow = prf(o.y.values == 2, o.cls.values == 2)
    v = pd.concat([v, pd.DataFrame([{"name": "Same as now", "high_precision": sameasnow[0],
                                     "high_recall": sameasnow[1], "high_f1": sameasnow[2]}])], ignore_index=True)
    return choice["name"], float(choice.t_high), float(choice.t_any), v


def _high_f1(d, col):
    return prf(d.y.values == 2, d[col].values == 2)[2]


def lmh_scores(train, test, features, t_high, t_any):
    """High precision/recall/F1, macro-F1 and top-10 case capture for the model and its baselines."""
    lr, gb = fit_lmh(train, features), fit_lmh_gbm(train, features)
    old = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, class_weight="balanced")) \
        .fit(train[LMH_FEATURES], train.y)
    p_lr, p_gb = lr.predict_proba(test[features]), gb.predict_proba(test[features])
    preds = {
        "Same as now": (test.cls.values, test.lrecent.values + test.cls.values),
        "Recent-history average": (np.rint(test.cls_last3).astype(int).values,
                                   test.cls_last3.values + 0.01 * test.lhist.values),
        "Previous model (balanced, most likely level)": (old.predict(test[LMH_FEATURES]),
                                                         old.predict_proba(test[LMH_FEATURES]) @ np.arange(3)),
        "Logistic regression": (to_level(p_lr, t_high, t_any), p_lr @ np.arange(3)),
        "Gradient boosting": (to_level(p_gb, t_high, t_any), p_gb @ np.arange(3)),
    }
    rows = []
    y = test.y.values
    for name, (p, s) in preds.items():
        cap = [g.nlargest(10, "s").y_recent.sum() / g.y_recent.sum()
               for _, g in test.assign(s=s).groupby("snapshot_date") if g.y_recent.sum() > 0]
        pr, rc, f1 = prf(y == 2, p == 2)
        rows.append({"model": name, "high_precision": pr, "high_recall": rc, "high_f1": f1,
                     "macro_f1": float(f1_score(y, p, average="macro")), "top10_capture": float(np.mean(cap))})
    t = test.assign(model=preds["Logistic regression"][0], same=test.cls.values)
    lo, hi = boot_diff(t, _high_f1, "model", "same")
    return pd.DataFrame(rows), (lo, hi)


# ------------------------------------------------------------------ new clusters

def clear(d):
    d = d[d.cls == 0].copy()
    d["e"] = (d.y > 0).astype(int)
    return d


def caught_at_5(d, score):
    """Share of new clusters that fell in the 5 highest-scored clear areas of their list."""
    d = d.assign(s=score)
    hit = sum(g.nlargest(5, "s").e.sum() for _, g in d.groupby("snapshot_date"))
    return float(hit / max(d.e.sum(), 1))


def precision_at_5(d, score):
    """Share of the 5 picks per list that did get a new cluster."""
    d = d.assign(s=score)
    g = [h.nlargest(5, "s").e for _, h in d.groupby("snapshot_date")]
    return float(sum(x.sum() for x in g) / max(sum(len(x) for x in g), 1))


def fit_new(train, features):
    c = clear(train)
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000)).fit(c[features], c.e)


def choose_new_set(dev):
    """Pick the feature set with the best backtest caught@5; ties (to 3 decimals) go to the smaller set."""
    c = clear(dev)
    rows = []
    for name, feats in NEW_SETS.items():
        o = backtest(c, lambda tr, va, f=feats: fit_new(tr, f).predict_proba(va[f])[:, 1])
        rows.append({"name": name, "caught_at_5": caught_at_5(o, o.score.values),
                     "precision_at_5": precision_at_5(o, o.score.values)})
    o = backtest(c, lambda tr, va: va.lhist.values)
    v = pd.DataFrame(rows)
    best = v.caught_at_5.round(3).max()
    choice = v[v.caught_at_5.round(3) == best].iloc[0]["name"]
    v = pd.concat([v, pd.DataFrame([{"name": "Past clusters here", "caught_at_5": caught_at_5(o, o.score.values),
                                     "precision_at_5": precision_at_5(o, o.score.values)}])], ignore_index=True)
    return choice, v


def new_scores(train, test, features):
    """caught@5, precision@5, ROC-AUC, PR-AUC for the chosen set, every other set and the simple baselines."""
    te = clear(test)

    def row(name, s):
        return (name, caught_at_5(te, s), precision_at_5(te, s), roc_auc_score(te.e, s), average_precision_score(te.e, s))
    # chance = expected value of 5 random picks (one random draw varied from 7% to 15% between data versions):
    # each new cluster is picked with probability min(5, clear areas) / clear areas
    g = te.groupby("snapshot_date").e.agg(["size", "sum"])
    rows = [("Chance (expected)", float((g["sum"] * g["size"].clip(upper=5) / g["size"]).sum() / g["sum"].sum()),
             float(te.e.mean()), 0.5, float(te.e.mean())),
            row("Most HDB homes", te.du.values),
            row("Past clusters here", te.lhist.values)]
    for name, feats in NEW_SETS.items():
        rows.append(row("Logistic: " + name, fit_new(train, feats).predict_proba(te[feats])[:, 1]))
    c = clear(train)
    gb = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=3, min_samples_leaf=30,
                                        random_state=0).fit(c[features], c.e)
    rows.append(row("Gradient boosting: chosen set", gb.predict_proba(te[features])[:, 1]))
    out = pd.DataFrame(rows, columns=["score", "caught_at_5", "precision_at_5", "roc_auc", "pr_auc"]).assign(
        n_new=int(te.e.sum()), n_clear=len(te))
    t = te.assign(model=fit_new(train, features).predict_proba(te[features])[:, 1], past=te.lhist.values)
    lo, hi = boot_diff(t, lambda d, col: caught_at_5(d, d[col].values), "model", "past")
    return out, (lo, hi)


# ------------------------------------------------------------------ live forecast

def forecast(df, lmh_model, lmh_features, t_high, t_any, new_model, new_features):
    """Score every area on the newest list date."""
    basis = df.snapshot_date.max()
    cur = df[df.snapshot_date == basis].copy()
    # a month NEA hasn't published yet counts as average weather (anomaly 0)
    cur[WEATHER] = cur[WEATHER].fillna(0.0)
    p = lmh_model.predict_proba(cur[lmh_features])
    cur["p_low"], cur["p_medium"], cur["p_high"] = p[:, 0], p[:, 1], p[:, 2]
    cur["predicted_level"] = [LEVELS[int(i)] for i in to_level(p, t_high, t_any)]
    cur["same_as_now_level"] = cur.cls.map(LEVELS)
    cur["new_cluster_score"] = np.where(cur.cls == 0, new_model.predict_proba(cur[new_features])[:, 1], np.nan)
    cur["new_cluster_rank"] = cur.new_cluster_score.rank(ascending=False, method="first")
    cur["past_cluster_score"] = cur.lhist
    cols = ["planning_area", "cls", "recent", "predicted_level", "p_low", "p_medium", "p_high",
            "same_as_now_level", "new_cluster_score", "new_cluster_rank", "past_cluster_score"]
    return basis, cur[cols].rename(columns={"cls": "level_today", "recent": "recent_cases_today"}) \
        .sort_values(["p_high", "new_cluster_score"], ascending=False)
