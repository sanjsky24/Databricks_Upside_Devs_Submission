"""DengueRadar models: area risk level (Low/Medium/High) and new-cluster score.

Plain pandas + scikit-learn so the same code runs in a Databricks notebook and on a laptop.
Input is the gold_area_fortnight table (one row per planning area per list date).

Honest evaluation, all of it rolling: a year is always predicted by models trained only on lists
whose outcome was known before 1 Jan of that year.
  1. choose feature sets and decision thresholds on 2017, 2018 and 2019 (history from Nov 2013);
  2. with those choices fixed, test every year from 2020 (2020, 2022, 2024, 2025, 2026; 2021 and
     2023 have no list with a known outcome), against the baselines with 90% intervals;
  3. retrain on every labelled row for the live forecast.
2024-26 alone (19 lists with an outcome) is too few to judge a model on, so it is reported as part
of the 2020-26 test, not on its own.

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

CHOOSE_YEARS = (2017, 2018, 2019)
TEST_YEARS = tuple(range(2020, 2027))
LEVELS = {0: "Low", 1: "Medium", 2: "High"}


def labelled(df):
    d = df[df.y.notna()].copy()
    d["y"] = d.y.astype(int)
    d["snapshot_date"] = pd.to_datetime(d.snapshot_date)
    d["next_date"] = pd.to_datetime(d.next_date)
    return d


def development(d):
    """Labelled rows whose outcome was known before the first test year."""
    return d[d.next_date < f"{TEST_YEARS[0]}-01-01"]


def rolling(d, years, fit_score):
    """Predict each year with fit_score(train, year_rows) -> {column: values}, where train holds only
    lists whose outcome (next_date) was known before 1 Jan of that year."""
    parts = []
    for y in years:
        start, end = pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y + 1}-01-01")
        tr, te = d[d.next_date < start], d[(d.snapshot_date >= start) & (d.snapshot_date < end)]
        if len(te) and len(tr):
            parts.append(te.assign(test_year=y, **fit_score(tr, te)))
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
    """90% interval of metric(a) - metric(b), resampling test list dates with replacement.
    A date drawn twice counts as two lists (otherwise caught@5 would pick 5 from the merged rows)."""
    lists = [g for _, g in test.groupby("snapshot_date")]
    rng = np.random.RandomState(seed)
    out = []
    for _ in range(n):
        s = pd.concat([lists[i].assign(snapshot_date=k) for k, i in enumerate(rng.randint(len(lists), size=len(lists)))])
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


def _proba(model, x):
    p = model.predict_proba(x)
    return {"p_low": p[:, 0], "p_medium": p[:, 1], "p_high": p[:, 2]}


def choose_lmh(dev):
    """Pick the risk-level feature set and both thresholds on the CHOOSE_YEARS backtest (ties go to the smaller set)."""
    rows = []
    for name, feats in LMH_SETS.items():
        o = rolling(dev, CHOOSE_YEARS, lambda tr, va, f=feats: _proba(fit_lmh(tr, f), va[f]))
        p = o[["p_low", "p_medium", "p_high"]].values
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


def lmh_scores(d, features, t_high, t_any):
    """Rolling test from 2020: High precision/recall/F1, macro-F1 and top-10 case capture for the model
    and its baselines; 90% interval of High F1 minus "same as now"; and the scored test rows."""
    def fit_score(tr, te):
        lr, gb = fit_lmh(tr, features), fit_lmh_gbm(tr, features)
        old = make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, class_weight="balanced")) \
            .fit(tr[LMH_FEATURES], tr.y)
        p_lr, p_gb = lr.predict_proba(te[features]), gb.predict_proba(te[features])
        return {"pred_lr": to_level(p_lr, t_high, t_any), "s_lr": p_lr @ np.arange(3), "p_high": p_lr[:, 2],
                "pred_gb": to_level(p_gb, t_high, t_any), "s_gb": p_gb @ np.arange(3),
                "pred_old": old.predict(te[LMH_FEATURES]), "s_old": old.predict_proba(te[LMH_FEATURES]) @ np.arange(3)}
    test = rolling(d, TEST_YEARS, fit_score)
    preds = {
        "Same as now": (test.cls.values, test.lrecent.values + test.cls.values),
        "Recent-history average": (np.rint(test.cls_last3).astype(int).values,
                                   test.cls_last3.values + 0.01 * test.lhist.values),
        "Previous model (balanced, most likely level)": (test.pred_old.values, test.s_old.values),
        "Logistic regression": (test.pred_lr.values, test.s_lr.values),
        "Gradient boosting": (test.pred_gb.values, test.s_gb.values),
    }
    rows = []
    y = test.y.values
    for name, (p, s) in preds.items():
        cap = [g.nlargest(10, "s").y_recent.sum() / g.y_recent.sum()
               for _, g in test.assign(s=s).groupby("snapshot_date") if g.y_recent.sum() > 0]
        pr, rc, f1 = prf(y == 2, p == 2)
        rows.append({"model": name, "high_precision": pr, "high_recall": rc, "high_f1": f1,
                     "macro_f1": float(f1_score(y, p, average="macro")), "top10_capture": float(np.mean(cap))})
    t = test.assign(model=test.pred_lr.values, same=test.cls.values)
    lo, hi = boot_diff(t, _high_f1, "model", "same")
    return pd.DataFrame(rows), (lo, hi), t


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
    """Pick the feature set with the best CHOOSE_YEARS backtest caught@5; ties (to 3 decimals) go to the smaller set."""
    c = clear(dev)
    rows = []
    for name, feats in NEW_SETS.items():
        o = rolling(c, CHOOSE_YEARS, lambda tr, va, f=feats: {"score": fit_new(tr, f).predict_proba(va[f])[:, 1]})
        rows.append({"name": name, "caught_at_5": caught_at_5(o, o.score.values),
                     "precision_at_5": precision_at_5(o, o.score.values)})
    o = rolling(c, CHOOSE_YEARS, lambda tr, va: {"score": va.lhist.values})
    v = pd.DataFrame(rows)
    best = v.caught_at_5.round(3).max()
    choice = v[v.caught_at_5.round(3) == best].iloc[0]["name"]
    v = pd.concat([v, pd.DataFrame([{"name": "Past clusters here", "caught_at_5": caught_at_5(o, o.score.values),
                                     "precision_at_5": precision_at_5(o, o.score.values)}])], ignore_index=True)
    return choice, v


def new_scores(d, new_set):
    """Rolling test from 2020: caught@5, precision@5, ROC-AUC, PR-AUC for every feature set (the chosen
    one is new_set), gradient boosting and the simple baselines; 90% interval of caught@5 minus
    "past clusters here"; and the scored test rows (clear areas only)."""
    features = NEW_SETS[new_set]

    def fit_score(tr, te):
        out = {f"s|{name}": fit_new(tr, feats).predict_proba(te[feats])[:, 1] for name, feats in NEW_SETS.items()}
        c = clear(tr)
        gb = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=3, min_samples_leaf=30,
                                            random_state=0).fit(c[features], c.e)
        out["s|gb"] = gb.predict_proba(te[features])[:, 1]
        return out
    te = rolling(clear(d), TEST_YEARS, fit_score)

    def row(name, s):
        return (name, caught_at_5(te, s), precision_at_5(te, s), roc_auc_score(te.e, s), average_precision_score(te.e, s))
    # chance = expected value of 5 random picks: each new cluster is picked with probability
    # min(5, clear areas) / clear areas
    g = te.groupby("snapshot_date").e.agg(["size", "sum"])
    rows = [("Chance (expected)", float((g["sum"] * g["size"].clip(upper=5) / g["size"]).sum() / g["sum"].sum()),
             float(te.e.mean()), 0.5, float(te.e.mean())),
            row("Most HDB homes", te.du.values),
            row("Past clusters here", te.lhist.values)]
    for name in NEW_SETS:
        rows.append(row("Logistic: " + name, te[f"s|{name}"].values))
    rows.append(row("Gradient boosting: chosen set", te["s|gb"].values))
    out = pd.DataFrame(rows, columns=["score", "caught_at_5", "precision_at_5", "roc_auc", "pr_auc"]).assign(
        n_new=int(te.e.sum()), n_clear=len(te))
    t = te.assign(model=te[f"s|{new_set}"].values, past=te.lhist.values)
    lo, hi = boot_diff(t, lambda x, col: caught_at_5(x, x[col].values), "model", "past")
    return out, (lo, hi), t


def by_year(lmh_test, new_test):
    """Model vs baseline for each test year (from the rows lmh_scores and new_scores return)."""
    rows = []
    for y, t in lmh_test.groupby("test_year"):
        n = new_test[new_test.test_year == y]
        rows.append({"year": y, "lists": t.snapshot_date.nunique(), "high_outcomes": int((t.y == 2).sum()),
                     "high_f1": _high_f1(t, "model"), "same_as_now_high_f1": _high_f1(t, "same"),
                     "new_clusters": int(n.e.sum()), "caught_at_5": caught_at_5(n, n.model.values),
                     "past_clusters_caught_at_5": caught_at_5(n, n.past.values)})
    return pd.DataFrame(rows)


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
