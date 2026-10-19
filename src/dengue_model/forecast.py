"""DengueRadar models: area risk level (Low/Medium/High) and new-cluster score.

Plain pandas + scikit-learn so the same code runs in a Databricks notebook and on a laptop.
Input is the gold_area_fortnight table (one row per planning area per list date).

Honest evaluation (fixes the prototype's selection bias):
  train 2015-2021 -> choose the new-cluster feature set on 2022-23 -> retrain on 2015-2023
  -> test ONCE on 2024-26 -> retrain on every labelled row for the live forecast.
"""

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, f1_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

LMH_FEATURES = ["cls", "lrecent", "ltotal", "n", "lhist", "cls_last3", "nbr", "nat", "du", "wol_dyn", "ms", "mc"]

# Candidate feature sets for the new-cluster score (areas with no cluster today), smallest first
NEW_SETS = {
    "history": ["lhist", "cls_last3"],
    "+ neighbours": ["lhist", "cls_last3", "nbr"],
    "+ homes": ["lhist", "cls_last3", "nbr", "du"],
    "+ Wolbachia (by release date)": ["lhist", "cls_last3", "nbr", "du", "wol_dyn"],
    "+ season & national": ["lhist", "cls_last3", "nbr", "du", "wol_dyn", "nat", "ms", "mc"],
}

TRAIN_END, VALID_END = "2022-01-01", "2024-01-01"
LEVELS = {0: "Low", 1: "Medium", 2: "High"}


def split(df):
    d = df[df.y.notna()].copy()
    d["y"] = d.y.astype(int)
    t = pd.to_datetime(d.snapshot_date)
    return d[t < TRAIN_END], d[(t >= TRAIN_END) & (t < VALID_END)], d[t >= VALID_END]


# ------------------------------------------------------------------ area risk level

def fit_lmh(train):
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000, class_weight="balanced")) \
        .fit(train[LMH_FEATURES], train.y)


def fit_lmh_gbm(train):
    return HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=3, min_samples_leaf=30,
                                          class_weight="balanced", random_state=0).fit(train[LMH_FEATURES], train.y)


def lmh_scores(train, test):
    """High recall/precision, macro-F1 and top-10 case capture for the model and its baselines."""
    lr, gb = fit_lmh(train), fit_lmh_gbm(train)
    preds = {
        "Same as now": (test.cls.values, test.lrecent.values + test.cls.values),
        "Recent-history average": (np.rint(test.cls_last3).astype(int).values,
                                   test.cls_last3.values + 0.01 * test.lhist.values),
        "Logistic regression": (lr.predict(test[LMH_FEATURES]), lr.predict_proba(test[LMH_FEATURES]) @ np.arange(3)),
        "Gradient boosting": (gb.predict(test[LMH_FEATURES]), gb.predict_proba(test[LMH_FEATURES]) @ np.arange(3)),
    }
    rows = []
    for name, (p, s) in preds.items():
        y = test.y.values
        cap = [g.nlargest(10, "s").y_recent.sum() / g.y_recent.sum()
               for _, g in test.assign(s=s).groupby("snapshot_date") if g.y_recent.sum() > 0]
        rows.append({"model": name,
                     "high_recall": float(np.mean(p[y == 2] == 2)),
                     "high_precision": float(np.mean(y[p == 2] == 2)) if (p == 2).any() else float("nan"),
                     "macro_f1": float(f1_score(y, p, average="macro")),
                     "top10_capture": float(np.mean(cap))})
    return pd.DataFrame(rows)


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


def fit_new(train, features):
    c = clear(train)
    return make_pipeline(StandardScaler(), LogisticRegression(max_iter=3000)).fit(c[features], c.e)


def new_scores(train, test, sets=NEW_SETS):
    """caught@5, ROC-AUC, PR-AUC for every feature set and the simple baselines."""
    te = clear(test)
    rows = [("Chance", caught_at_5(te, np.random.RandomState(0).rand(len(te))), 0.5, te.e.mean()),
            ("Most HDB homes", caught_at_5(te, te.du.values), roc_auc_score(te.e, te.du),
             average_precision_score(te.e, te.du)),
            ("Past clusters here", caught_at_5(te, te.lhist.values), roc_auc_score(te.e, te.lhist),
             average_precision_score(te.e, te.lhist))]
    for name, feats in sets.items():
        s = fit_new(train, feats).predict_proba(te[feats])[:, 1]
        rows.append(("Logistic: " + name, caught_at_5(te, s), roc_auc_score(te.e, s), average_precision_score(te.e, s)))
    feats = list(sets.values())[-1]
    c = clear(train)
    gb = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.05, max_depth=3, min_samples_leaf=30,
                                        random_state=0).fit(c[feats], c.e)
    s = gb.predict_proba(te[feats])[:, 1]
    rows.append(("Gradient boosting: all", caught_at_5(te, s), roc_auc_score(te.e, s), average_precision_score(te.e, s)))
    return pd.DataFrame(rows, columns=["score", "caught_at_5", "roc_auc", "pr_auc"]).assign(
        n_new=int(te.e.sum()), n_clear=len(te))


def choose_new_set(train, valid):
    """Pick the feature set with the best caught@5 on the validation years; ties go to the smaller set."""
    v = new_scores(train, valid)
    v = v[v.score.str.startswith("Logistic: ")].copy()
    v["name"] = v.score.str.replace("Logistic: ", "", regex=False)
    best = v.caught_at_5.max()
    return next(n for n in NEW_SETS if v.set_index("name").caught_at_5[n] == best), v


# ------------------------------------------------------------------ live forecast

def forecast(df, lmh_model, new_model, new_features):
    """Score every area on the newest list date."""
    basis = df.snapshot_date.max()
    cur = df[df.snapshot_date == basis].copy()
    p = lmh_model.predict_proba(cur[LMH_FEATURES])
    cur["p_low"], cur["p_medium"], cur["p_high"] = p[:, 0], p[:, 1], p[:, 2]
    cur["predicted_level"] = [LEVELS[int(i)] for i in p.argmax(1)]
    cur["same_as_now_level"] = cur.cls.map(LEVELS)
    cur["new_cluster_score"] = np.where(cur.cls == 0, new_model.predict_proba(cur[new_features])[:, 1], np.nan)
    cur["new_cluster_rank"] = cur.new_cluster_score.rank(ascending=False, method="first")
    cur["past_cluster_score"] = cur.lhist
    cols = ["planning_area", "cls", "recent", "predicted_level", "p_low", "p_medium", "p_high",
            "same_as_now_level", "new_cluster_score", "new_cluster_rank", "past_cluster_score"]
    return basis, cur[cols].rename(columns={"cls": "level_today", "recent": "recent_cases_today"}) \
        .sort_values(["p_high", "new_cluster_score"], ascending=False)
