import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import update_shops as u  # noqa: E402

HEADER = ["country", "province", "town_id", "town", "id", "name", "lang", "street",
          "latitude", "longitude", "sample_discount_percentage"]


def feed_text(rows, delim="\t"):
    lines = [delim.join(HEADER)]
    for r in rows:
        lines.append(delim.join(str(r[h]) for h in HEADER))
    return "\n".join(lines)


def row(i, town, name, lat, lng, country="FR", lang="fr", street="1 rue des Pistes"):
    return dict(country=country, province="X", town_id=town, town="Ville " + town, id=i,
                name=name, lang=lang, street=street, latitude=lat, longitude=lng,
                sample_discount_percentage="0.4")


def station(name, center, shops):
    return {"name": name, "center": center, "zoom": 13, "shops": [
        {"id": i, "name": n, "lat": center[0], "lng": center[1], "address": "x", "website": "w"}
        for i, n in shops]}


class Base(unittest.TestCase):
    def setUp(self):
        self.data = {"stations": {
            "alpha": station("Alpha", [45.0, 6.0], [("1", "Bravo"), ("2", "Alpha Sport")]),
            "beta": station("Beta", [45.5, 6.5], [("3", "Charlie")]),
            "gamma": station("Gamma", [46.0, 7.0], [("4", "Delta")]),
        }}
        self.rows = [
            row("1", "10", "Bravo", 45.0, 6.0), row("2", "10", "Alpha Sport", 45.0, 6.01),
            row("3", "20", "Charlie", 45.5, 6.5), row("4", "30", "Delta", 46.0, 7.0),
            row("99", "10", "Nouveau", 45.0, 6.02), row("98", "99", "Ailleurs", 40.0, 1.0),
            row("97", "10", "Suisse", 45.0, 6.0, country="CH"),
            row("96", "10", "Anglais", 45.0, 6.0, lang="en"),
        ]


class TestParse(Base):
    def test_filters_and_delimiters(self):
        for d in ("\t", ";"):
            feed = u.parse_feed(feed_text(self.rows, d))
            self.assertEqual(sorted(s["id"] for s in feed), ["1", "2", "3", "4", "98", "99"])

    def test_missing_column(self):
        with self.assertRaises(u.FeedError):
            u.parse_feed("country\tlang\tid\ta\tb\tc\n")

    def test_empty(self):
        with self.assertRaises(u.FeedError):
            u.parse_feed(feed_text([row("1", "1", "N", "x", "y")]))


class TestRebuild(Base):
    def run_rebuild(self, rows=None, mapping=None):
        feed = u.parse_feed(feed_text(rows or self.rows))
        mapping = mapping or u.bootstrap_mapping(self.data, feed)
        return u.rebuild(self.data, mapping, feed), mapping

    def test_bootstrap_mapping(self):
        _, mapping = self.run_rebuild()
        self.assertEqual(mapping, {"alpha": ["10"], "beta": ["20"], "gamma": ["30"]})

    def test_adds_new_shop_sorted_and_links(self):
        (new, report), _ = self.run_rebuild()
        names = [s["name"] for s in new["stations"]["alpha"]["shops"]]
        self.assertEqual(names, ["Alpha Sport", "Bravo", "Nouveau"])
        self.assertEqual(report["added"], {"alpha": ["99"]})
        self.assertEqual(new["stations"]["alpha"]["shops"][2]["website"],
                         "https://www.alpy.com/fr/location-ski/parameters/99?referer=skiquality.com")
        self.assertEqual(report["new_towns"], [["99", "Ville 99", 1]])

    def test_removes_closed_shop(self):
        rows = [r for r in self.rows if r["id"] != "2"]
        (new, report), _ = self.run_rebuild(rows, {"alpha": ["10"], "beta": ["20"], "gamma": ["30"]})
        self.assertEqual([s["id"] for s in new["stations"]["alpha"]["shops"]], ["1", "99"])
        self.assertEqual(report["removed"], {"alpha": ["2"]})

    def test_shared_town_is_sticky_then_nearest(self):
        mapping = {"alpha": ["10"], "beta": ["10", "20"], "gamma": ["30"]}
        rows = self.rows + [row("50", "10", "Pres de Beta", 45.5, 6.5)]
        (new, _), _ = self.run_rebuild(rows, mapping)
        ids = lambda s: [x["id"] for x in new["stations"][s]["shops"]]  # noqa: E731
        self.assertIn("1", ids("alpha"))          # reste où il était
        self.assertIn("50", ids("beta"))          # nouveau : station la plus proche
        self.assertNotIn("50", ids("alpha"))

    def test_empty_result_keeps_old_list(self):
        rows = [r for r in self.rows if r["id"] != "4"]
        (new, report), _ = self.run_rebuild(rows, {"alpha": ["10"], "beta": ["20"], "gamma": ["30"]})
        self.assertEqual(report["kept_stale"], ["gamma"])
        self.assertEqual(new["stations"]["gamma"]["shops"], self.data["stations"]["gamma"]["shops"])

    def test_unmapped_station_untouched(self):
        (new, report), _ = self.run_rebuild(mapping={"alpha": ["10"], "beta": ["20"], "gamma": []})
        self.assertEqual(report["unmapped"], ["gamma"])
        self.assertEqual(new["stations"]["gamma"], self.data["stations"]["gamma"])

    def test_input_not_mutated(self):
        before = copy.deepcopy(self.data)
        self.run_rebuild()
        self.assertEqual(before, self.data)


class TestCli(Base):
    def test_end_to_end_and_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            shops, towns, feed = (os.path.join(tmp, n) for n in ("shops.json", "towns.json", "feed.csv"))
            with open(shops, "w", encoding="utf-8") as f:
                f.write(u.dump(self.data))
            with open(feed, "w", encoding="utf-8") as f:
                f.write(feed_text(self.rows))
            args = ["--shops", shops, "--towns", towns, "--feed-file", feed]
            self.assertEqual(u.main(args), 0)
            self.assertTrue(os.path.exists(towns))
            out = json.load(open(shops, encoding="utf-8"))
            self.assertEqual(len(out["stations"]["alpha"]["shops"]), 3)
            # 2e passage : stable
            before = open(shops, encoding="utf-8").read()
            self.assertEqual(u.main(args), 0)
            self.assertEqual(before, open(shops, encoding="utf-8").read())
            # flux quasi vide -> garde-fou, fichier inchangé
            with open(feed, "w", encoding="utf-8") as f:
                f.write(feed_text([self.rows[0]]))
            self.assertEqual(u.main(args), 1)
            self.assertEqual(before, open(shops, encoding="utf-8").read())


if __name__ == "__main__":
    unittest.main()
