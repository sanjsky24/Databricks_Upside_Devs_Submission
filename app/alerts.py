"""Draft dengue alerts with a Databricks-hosted LLM, grounded in this week's data.

The model only sees a facts block built from the app's data (the area's forecast, today's clusters and
case counts, the HDB blocks near them, Wolbachia cover, dates) plus NEA's standard advice. After drafting,
every number in the text is checked against the facts; numbers that aren't there are returned so the
officer can fix them before sending.
"""

import json
import os
import re
from collections import defaultdict
from datetime import date

ENDPOINT = os.environ.get("LLM_ENDPOINT", "databricks-meta-llama-3-3-70b-instruct")

AUDIENCES = {
    "residents": "residents of the HDB blocks listed in the facts",
    "town_council": "the town council's estate officers (operational: what to check, where, by when)",
    "grassroots": "grassroots and Residents' Committee leaders who will pass the message on",
}
CHANNELS = {
    "sms": ("an SMS or WhatsApp broadcast of at most 320 characters, no subject line. Name the area and the street(s) "
            "or cluster location, say what the risk is in plain words, and give two of the B-L-O-C-K steps written out "
            "in full (not just the letters)"),
    "notice": ("a notice for HDB lift lobbies: a short title line, then 100-160 words: what is happening here (from the "
               "facts), the blocks it covers, the B-L-O-C-K steps as a list, and when to see a doctor"),
    "email": ("an email with a 'Subject:' line, then 120-200 words: what the facts say, the blocks to prioritise, and "
              "the suggested actions for this audience"),
}
ACTIONS = {
    "residents": "follow the B-L-O-C-K steps at home, use insect repellent, see a doctor early if unwell",
    "town_council": ("inspect common corridors, roof gutters, scupper drains and bin chutes in the listed blocks; put up "
                     "notices in their lift lobbies; remind residents of the B-L-O-C-K steps"),
    "grassroots": ("share the message with residents of the listed blocks (chat groups, door-to-door), and remind them "
                   "of the B-L-O-C-K steps"),
}
LANGUAGES = {"en": "English", "zh": "Simplified Chinese", "ms": "Malay", "ta": "Tamil"}

NEA_ADVICE = ("NEA's Mozzie Wipeout B-L-O-C-K steps: Break up hardened soil; Lift and empty flowerpot plates; "
              "Overturn pails and wipe their rims; Change water in vases; Keep roof gutters clear and place BTI "
              "insecticide inside. Apply insect repellent. See a doctor early if you have fever, severe headache, "
              "pain behind the eyes, joint or muscle pain, or a rash.")

SYSTEM = (
    "You draft dengue public-health messages for Singapore town councils and NEA officers. "
    "Use ONLY the facts given. Never invent numbers, dates, block numbers, street names, places, deadlines, "
    "contact details or reporting requests; if something is not in the facts, leave it out. Copy block numbers and "
    "street names exactly. Only suggest the actions listed for the audience. Be calm, specific and practical: no "
    "words like 'urgent', 'immediate action' or 'outbreak' unless the facts say so. If the area is Low risk today, "
    "say so plainly and explain why it is still on watch. Write the whole message in the requested language (keep "
    "block numbers and street names as given; translate the advice and B-L-O-C-K steps too; use Simplified "
    "characters for Chinese). "
    "Output only the message text, nothing else."
)


def _blocks_by_street(blocks):
    by = defaultdict(list)
    for b in blocks:
        by[str(b["street"]).title()].append(str(b["blk_no"]))
    return [f"Blk {', '.join(sorted(v, key=lambda s: (len(s), s))[:12])} {k}" for k, v in
            sorted(by.items(), key=lambda kv: -len(kv[1]))[:6]]


def facts(data, area, site=None):
    """The facts block for one planning area (and optionally one team site in it)."""
    a = next((x for x in data["areas"] if x["planning_area"] == area), None)
    if a is None:
        raise ValueError(f"unknown area {area}")
    clusters = [c for c in data["clusters"] if c.get("planning_area") == area]
    near = [b for b in data["blocks"] if b["planning_area"] == area and b["near_cluster"] in (True, "true")]
    chosen = site["blocks"] if site else (near or sorted(
        (b for b in data["blocks"] if b["planning_area"] == area), key=lambda b: -float(b["weight"]))[:20])
    meta = data["meta"]
    f = {
        "area": a["area"],
        "today": date.today().isoformat(),
        "nea_cluster_list_date": meta.get("cluster_list_date"),
        "risk_level_today": a["level_today_name"],
        "risk_level_forecast_in_two_weeks": a["predicted_level"],
        "chance_of_high_risk_in_two_weeks_percent": round(100 * float(a["p_high"] or 0)),
        "active_dengue_clusters_in_area_today": [
            {"locality": c["locality"], "cases_since_cluster_started": c["total"],
             "cases_in_last_2_weeks": c.get("recent")} for c in clusters],
        "cases_in_last_2_weeks_in_area": a["recent_cases"],
        "recent_cases_in_neighbouring_areas": a["recent_next_door"],
        "share_of_homes_in_wolbachia_release_zones_percent": round(100 * float(a["wolbachia_share"] or 0)),
        "blocks_to_notify": _blocks_by_street(chosen),
        "homes_in_those_blocks": int(sum(int(b["homes"]) for b in chosen)),
        "number_of_blocks": len(chosen),
    }
    if a.get("new_cluster_rank") and int(a["new_cluster_rank"]) <= 5 and int(a["level_today"]) == 0:
        f["new_cluster_watch"] = (f"No cluster here today, but ranked #{int(a['new_cluster_rank'])} of the clear areas "
                                  "most likely to see a new cluster in the next two weeks")
    if site:
        f["inspection_site"] = f"Inspection team site centred on {site['anchor']}"
    return f


def draft(client, f, audience, channel, language):
    user = (f"Write {CHANNELS[channel]}, for {AUDIENCES[audience]}, in {LANGUAGES[language]}.\n\n"
            f"Facts (JSON):\n{json.dumps(f, ensure_ascii=False, indent=1)}\n\n"
            f"Actions you may suggest to this audience: {ACTIONS[audience]}.\n\nAdvice you may use:\n{NEA_ADVICE}")
    r = client.api_client.do("POST", f"/serving-endpoints/{ENDPOINT}/invocations", body={
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "max_tokens": 700, "temperature": 0.1})
    text = r["choices"][0]["message"]["content"].strip()
    return text, unverified_numbers(text, f)


def unverified_numbers(text, f):
    """Numbers in the draft that don't appear anywhere in the facts (dates are checked as day numbers)."""
    allowed = set(re.findall(r"\d+", json.dumps(f, ensure_ascii=False))) | {"2", "14"}  # "2 weeks", "14 days"
    for d in (f.get("today"), f.get("nea_cluster_list_date")):
        if d:
            y, m, dd = d.split("-")
            allowed |= {y, str(int(m)), str(int(dd))}
    return sorted({n for n in re.findall(r"\d+", text) if n.lstrip("0") not in {a.lstrip("0") for a in allowed}},
                  key=int)
