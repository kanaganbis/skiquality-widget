# skiquality-widget

Carte et liste des magasins de location de ski Alpy affichées sur les pages stations de SkiQuality.

- `widget.js` / `widget.css` : le widget (Leaflet), chargé depuis jsDelivr.
- `shops.json` : magasins par station (clé = slug de la station dans le CMS Webflow).
  **Régénéré automatiquement** chaque lundi, ne pas l'éditer à la main.
- `stations-towns.json` : relie chaque station à une ou plusieurs « villes » Alpy (`town_id`). Créé au premier passage du script.
- `scripts/update_shops.py` : lit le flux Alpy (Worker Cloudflare), reconstruit `shops.json`.
- `.github/workflows/update-shops.yml` : lance le script chaque semaine, ou à la demande.

## Lancer une mise à jour à la main
GitHub → onglet **Actions** → « Mise à jour des magasins Alpy » → **Run workflow**.
Par défaut c'est une simulation (rien n'est modifié, le résumé s'affiche dans le run).
Décocher « Simulation » pour appliquer.

## Ajouter une station
1. Dans `shops.json`, ajouter l'entrée de la station (slug identique au CMS) avec `name`, `center`, `zoom` et `"shops": []`.
2. Dans `stations-towns.json`, ajouter `"slug-de-la-station": ["<town_id Alpy>"]`.
   Le `town_id` se lit dans le résumé du run (« Villes Alpy pas encore reliées à une station »).
3. Lancer la mise à jour.

## Garde-fous
- Le fichier n'est pas modifié si le nombre total de magasins chute de plus de 20 %.
- Si le flux ne renvoie plus aucun magasin pour une station, son ancienne liste est conservée.
- Les liens gardent `?referer=skiquality.com`.
