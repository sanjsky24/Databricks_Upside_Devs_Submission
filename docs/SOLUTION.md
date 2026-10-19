# DengueRadar, explained simply

## The problem

Singapore has a powerful dengue weapon: Wolbachia mosquitoes. NEA releases them in neighbourhoods, and they
stop the local mosquitoes from spreading dengue. It works. Today 67% of HDB homes are in a Wolbachia area.

So dengue moved. On 1 Oct 2026, **97% of cases in active clusters were outside Wolbachia areas.** Ten years ago,
those same areas held about two-thirds of cases. Now they hold almost none.

NEA's map shows where clusters are **today**. Nobody's map shows where the **next one** will start. By the time a
cluster appears, people are already sick.

## What DengueRadar does

Every Monday, for each of Singapore's 55 planning areas, it answers one question:

> **How likely is a dengue cluster here in the next two weeks?**

It answers in NEA's own colours, so officers don't have to learn anything new:

- **High** – a red cluster (10+ cases) is likely
- **Medium** – a yellow cluster (under 10 cases) is likely
- **Low** – no cluster expected

It also does two more things:

1. **Warns about quiet areas.** Areas with no cluster today get a separate "new cluster" score. This is the useful
   part: anyone can guess that an area with a cluster today will still have one next week.
2. **Plans the week.** A town council with six inspection teams gets six addresses to send them to, chosen to cover
   the most at-risk homes.

## Who it's for

A town-council or NEA officer on Monday morning, deciding where to send inspection teams before clusters form.

## How it works

**1. Collect.** Every day a Databricks job saves NEA's cluster list. NEA doesn't keep old lists online, so we're
building that history ourselves. We also have 11 years of past lists (2015 to 2026), recovered from public archives.

**2. Clean.** Every past cluster is put on the map and matched to its neighbourhood. We also know when Wolbachia
started in each of its 31 areas, so we know which homes were protected on any given day.

**3. Learn.** For every neighbourhood and every past list, we record what was known that day (recent cases, past
outbreaks, nearby cases, Wolbachia cover, season) and what happened two weeks later. That's about 15,000 examples.
A simple model learns which signs come before trouble.

**4. Test honestly.** The model learns only from older years, then is tested on 2024–26, which it has never seen. It
has to beat the obvious guess: **"same as now"** (whatever the area is today, it stays that way). That guess is
hard to beat, because clusters stay open for at least two weeks.

**5. Prove it in public.** We lock a forecast in a table that can't be edited, then check it against what NEA
actually reports. The first one will be locked before Demo Day and checked live on 27 Oct.

## How good is it?

Tested on 2024–26, years the model never saw while learning:

- **High-risk areas:** catches 69%, versus 62% for "same as now".
- **New clusters:** each fortnight, its top 5 picks among quiet areas catch 39% of the new clusters that appear.
  Random picks catch 7%, so that's about 5× better than chance. Simply ranking areas by past clusters gets 38%, so
  the model's edge over that simple rule is small. Be honest about this if asked.
- **Team planner** (prototype): six teams reach 2.6× more at-risk homes than a simple plan.

The prototype said 44% for new clusters. That figure came from picking the best of several tries by looking at the
test years. The honest re-test picks on 2022–23 first and tests once, which gives 39%. **Quote 39%.**

## What it is not

- **Not a national case forecast.** We tried that. It can't beat "same as now", so it's background only.
- **Not a replacement for NEA.** It points teams at places; people still do the inspections.
- **Not a black box.** Every forecast comes with its reasons ("3 clusters nearby, no Wolbachia here").

## Words you'll hear

| Word | Meaning |
|---|---|
| Cluster | NEA's term: 2+ cases within 150 m, starting within 14 days of each other |
| Planning area | One of 55 official neighbourhoods (Tampines, Bishan, ...) |
| Wolbachia area | A neighbourhood where NEA releases Wolbachia mosquitoes |
| Bronze / silver / gold | Raw downloads → cleaned history → the model's table |
| Baseline | A simple guess the model must beat, like "same as now" |
| Locked forecast | A prediction saved before the outcome is known, so nobody can tweak it after |

All cluster data comes from NEA. Credit them whenever you show it.
