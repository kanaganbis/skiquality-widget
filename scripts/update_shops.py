#!/usr/bin/env python3
"""Régénère shops.json à partir du flux Alpy (Worker Cloudflare de SkiQuality).

Principe
- Le flux Alpy liste tous les magasins avec leur « ville » Alpy (town_id).
- stations-towns.json relie chaque station SkiQuality (slug du CMS) à une ou
  plusieurs villes Alpy. S'il n'existe pas encore, il est déduit de
  shops.json (les villes des magasins actuels de chaque station).
- Pour chaque station, la liste des magasins est reconstruite : nouveaux
  magasins ajoutés, magasins disparus du flux retirés, noms / adresses /
  positions mis à jour.

Aucune dépendance externe (bibliothèque standard uniquement).
"""
import argparse
import csv
import io
import json
import math
import os
import sys
import urllib.request

FEED_URL = "https://skiquality-alpy.doraine.workers.dev/"
REFERER = "skiquality.com"
# Garde-fou : on n'écrase rien si le total de magasins chute de plus de 20 %.
MIN_RATIO = 0.8
REQUIRED = ["country", "lang", "id", "name", "latitude", "longitude", "town_id"]


class FeedError(Exception):
    pass


def fetch_feed(url):
    req = urllib.request.Request(url, headers={"User-Agent": "skiquality-widget-updater"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8-sig")


def parse_feed(text):
    """Retourne les magasins français (langue fr), dédoublonnés par id."""
    text = text.lstrip("﻿")
    header = text.split("\n", 1)[0]
    delim = max(["\t", ";", ","], key=header.count)
    if header.count(delim) < 5:
        raise FeedError("En-tête du flux illisible : " + header[:120])
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    missing = [c for c in REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise FeedError("Colonnes absentes du flux : " + ", ".join(missing))
    shops, seen = [], set()
    for row in reader:
        if row.get("country") != "FR" or row.get("lang") != "fr":
            continue
        sid = (row.get("id") or "").strip()
        if not sid or sid in seen:
            continue
        try:
            lat, lng = float(row["latitude"]), float(row["longitude"])
        except (TypeError, ValueError):
            continue
        seen.add(sid)
        shops.append({
            "id": sid,
            "name": (row.get("name") or "").strip(),
            "lat": lat,
            "lng": lng,
            "address": (row.get("street") or "").strip(),
            "town_id": (row.get("town_id") or "").strip(),
            "town": (row.get("town") or "").strip(),
        })
    if not shops:
        raise FeedError("Aucun magasin français dans le flux.")
    return shops


def haversine(lat1, lng1, lat2, lng2):
    r, p = 6371000, math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def bootstrap_mapping(data, feed):
    """station -> villes Alpy, déduit des magasins actuellement affichés."""
    town_of = {s["id"]: s["town_id"] for s in feed}
    mapping = {}
    for slug, st in data["stations"].items():
        mapping[slug] = sorted({town_of[s["id"]] for s in st["shops"] if s["id"] in town_of},
                               key=lambda t: (len(t), t))
    return mapping


def shop_entry(s):
    return {
        "id": s["id"],
        "name": s["name"],
        "lat": s["lat"],
        "lng": s["lng"],
        "address": s["address"],
        "website": "https://www.alpy.com/fr/location-ski/parameters/%s?referer=%s" % (s["id"], REFERER),
    }


def rebuild(data, mapping, feed):
    """Retourne (nouveau_data, rapport)."""
    stations = data["stations"]
    towns_to_stations = {}
    for slug, towns in mapping.items():
        if slug in stations:
            for t in towns:
                towns_to_stations.setdefault(t, []).append(slug)

    current = {s["id"]: slug for slug, st in stations.items() for s in st["shops"]}
    assigned = {slug: [] for slug in stations}
    for s in feed:
        cands = towns_to_stations.get(s["town_id"])
        if not cands:
            continue
        if current.get(s["id"]) in cands:
            slug = current[s["id"]]
        elif len(cands) == 1:
            slug = cands[0]
        else:
            slug = min(cands, key=lambda c: haversine(
                s["lat"], s["lng"], stations[c]["center"][0], stations[c]["center"][1]))
        assigned[slug].append(s)

    out = {"stations": {}}
    report = {"added": {}, "removed": {}, "kept_stale": [], "unmapped": []}
    for slug, st in stations.items():
        new_st = dict(st)
        old_ids = {s["id"] for s in st["shops"]}
        if not mapping.get(slug):
            report["unmapped"].append(slug)
            out["stations"][slug] = new_st
            continue
        shops = sorted(assigned[slug], key=lambda s: (s["name"].lower(), s["id"]))
        if not shops and st["shops"]:
            report["kept_stale"].append(slug)  # mieux vaut une liste ancienne que vide
            out["stations"][slug] = new_st
            continue
        new_st["shops"] = [shop_entry(s) for s in shops]
        new_ids = {s["id"] for s in shops}
        if new_ids - old_ids:
            report["added"][slug] = sorted(new_ids - old_ids)
        if old_ids - new_ids:
            report["removed"][slug] = sorted(old_ids - new_ids)
        out["stations"][slug] = new_st
    mapped_towns = set(towns_to_stations)
    new_towns = {}
    for s in feed:
        if s["town_id"] not in mapped_towns:
            t = new_towns.setdefault(s["town_id"], [s["town"], 0])
            t[1] += 1
    report["new_towns"] = sorted(([k, v[0], v[1]] for k, v in new_towns.items()),
                                 key=lambda x: -x[2])
    return out, report


def count_shops(data):
    return sum(len(st["shops"]) for st in data["stations"].values())


def dump(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def summary(report, old_total, new_total):
    lines = ["## Mise à jour des magasins Alpy", "",
             "Magasins : %d → %d" % (old_total, new_total), ""]
    for title, key in (("Ajoutés", "added"), ("Retirés (absents du flux)", "removed")):
        if report[key]:
            lines.append("**%s**" % title)
            for slug, ids in sorted(report[key].items()):
                lines.append("- %s : %s" % (slug, ", ".join(ids)))
            lines.append("")
    if report["kept_stale"]:
        lines += ["**Liste conservée (le flux ne renvoie plus rien)** : "
                  + ", ".join(report["kept_stale"]), ""]
    if report["unmapped"]:
        lines += ["**Stations sans ville Alpy associée (non mises à jour)** : "
                  + ", ".join(report["unmapped"]), ""]
    if report["new_towns"]:
        lines.append("**Villes Alpy pas encore reliées à une station** (les plus fournies) :")
        for tid, name, n in report["new_towns"][:25]:
            lines.append("- %s (town_id %s) : %d magasins" % (name, tid, n))
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--feed-url", default=FEED_URL)
    ap.add_argument("--feed-file", help="fichier local à la place du flux (tests)")
    ap.add_argument("--shops", default="shops.json")
    ap.add_argument("--towns", default="stations-towns.json")
    ap.add_argument("--dry-run", action="store_true", help="n'écrit aucun fichier")
    args = ap.parse_args(argv)

    with open(args.shops, encoding="utf-8") as f:
        data = json.load(f)

    if args.feed_file:
        with open(args.feed_file, encoding="utf-8") as f:
            text = f.read()
    else:
        text = fetch_feed(args.feed_url)
    feed = parse_feed(text)

    first_run = not os.path.exists(args.towns)
    if first_run:
        mapping = bootstrap_mapping(data, feed)
        print("stations-towns.json absent : déduit de shops.json.")
    else:
        with open(args.towns, encoding="utf-8") as f:
            mapping = json.load(f)

    new_data, report = rebuild(data, mapping, feed)
    old_total, new_total = count_shops(data), count_shops(new_data)
    text_out = summary(report, old_total, new_total)
    print(text_out)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text_out + "\n")

    if new_total < old_total * MIN_RATIO:
        print("ERREUR : %d magasins au lieu de %d, fichier non modifié." % (new_total, old_total),
              file=sys.stderr)
        return 1
    if args.dry_run:
        return 0
    if first_run:
        with open(args.towns, "w", encoding="utf-8") as f:
            f.write(json.dumps(mapping, indent=2, ensure_ascii=False, sort_keys=True))
    with open(args.shops, "w", encoding="utf-8") as f:
        f.write(dump(new_data))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except FeedError as e:
        print("ERREUR flux Alpy : %s" % e, file=sys.stderr)
        sys.exit(1)
