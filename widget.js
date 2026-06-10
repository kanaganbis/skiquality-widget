/* SkiQuality - Carte magasins de location de ski
 * Usage: <div id="sq-shops" data-station="abondance" data-src="/path/to/shops.json"></div>
 */
(function () {
  'use strict';

  var ALPY_WORKER_URL = 'https://skiquality-alpy.doraine.workers.dev/';
  var alpyShopsCache = null;
  var alpyPromise = null;

  function init(container) {
    var stationSlug = container.dataset.station;
    var dataSrc = container.dataset.src || 'shops.json';

    if (!stationSlug) {
      container.innerHTML = '<div class="sq-shops-empty">Slug de station manquant (data-station).</div>';
      return;
    }

    container.classList.add('sq-shops-widget');
    container.innerHTML = '<div class="sq-shops-loading">Chargement de la carte…</div>';

    Promise.all([
      fetch(dataSrc, { cache: 'reload' }).then(function (r) {
        if (!r.ok) throw new Error('Fichier ' + dataSrc + ' introuvable (' + r.status + ')');
        return r.json();
      }),
      loadAlpyShops().catch(function (err) {
        console.warn('[SQ Shops] Alpy indisponible, remises masquées :', err.message);
        return [];
      })
    ])
      .then(function (results) {
        render(container, results[0], stationSlug, results[1]);
      })
      .catch(function (err) {
        console.error('[SQ Shops]', err);
        container.innerHTML = '<div class="sq-shops-empty">Impossible de charger la liste des magasins.<br><small>' + err.message + '</small></div>';
      });
  }

  function loadAlpyShops() {
    if (alpyShopsCache) return Promise.resolve(alpyShopsCache);
    if (alpyPromise) return alpyPromise;
    alpyPromise = fetch(ALPY_WORKER_URL)
      .then(function (r) {
        if (!r.ok) throw new Error('Alpy Worker HTTP ' + r.status);
        return r.text();
      })
      .then(function (csv) {
        alpyShopsCache = parseAlpyCSV(csv);
        return alpyShopsCache;
      });
    return alpyPromise;
  }

  function parseAlpyCSV(text) {
    var lines = text.split('\n');
    var shops = [];
    var seen = {};
    for (var i = 1; i < lines.length; i++) {
      var cols = [], cur = '', inQ = false;
      for (var c = 0; c < lines[i].length; c++) {
        var ch = lines[i][c];
        if (ch === '"') inQ = !inQ;
        else if (ch === ';' && !inQ) { cols.push(cur.replace(/^"|"$/g, '').trim()); cur = ''; }
        else cur += ch;
      }
      cols.push(cur.replace(/^"|"$/g, '').trim());
      if (cols.length < 20 || cols[6] !== 'fr' || cols[0] !== 'FR') continue;
      var id = cols[4];
      if (seen[id]) continue;
      seen[id] = true;
      var lat = parseFloat(cols[8]);
      var lng = parseFloat(cols[9]);
      if (isNaN(lat) || isNaN(lng)) continue;
      shops.push({
        id: id,
        name: cols[5],
        nameNorm: normName(cols[5]),
        lat: lat,
        lng: lng,
        discount: Math.round(parseFloat(cols[19] || 0) * 100)
      });
    }
    return shops;
  }

  function normName(s) {
    return String(s || '').toLowerCase()
      .replace(/[éèêë]/g, 'e').replace(/[îï]/g, 'i')
      .replace(/[ôö]/g, 'o').replace(/[àâä]/g, 'a')
      .replace(/[ùûü]/g, 'u').replace(/ç/g, 'c')
      .replace(/[^a-z0-9]+/g, '');
  }

  function findAlpyMatch(shop, alpyShops) {
    if (!alpyShops || !alpyShops.length) return null;
    var target = normName(shop.name);
    var bestByDist = null;
    var bestDist = Infinity;
    for (var i = 0; i < alpyShops.length; i++) {
      var a = alpyShops[i];
      var d = haversine(shop.lat, shop.lng, a.lat, a.lng);
      if (a.nameNorm === target && d < 3000) return a;
      if (d < 150 && d < bestDist) { bestByDist = a; bestDist = d; }
    }
    return bestByDist;
  }

  function haversine(lat1, lng1, lat2, lng2) {
    var R = 6371000;
    var dL = (lat2 - lat1) * Math.PI / 180;
    var dG = (lng2 - lng1) * Math.PI / 180;
    var x = Math.sin(dL / 2) * Math.sin(dL / 2) +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dG / 2) * Math.sin(dG / 2);
    return Math.round(R * 2 * Math.atan2(Math.sqrt(x), Math.sqrt(1 - x)));
  }

  function render(container, data, slug, alpyShops) {
    var station = data.stations && data.stations[slug];

    if (!station || !station.shops || !station.shops.length) {
      container.innerHTML =
        '<div class="sq-shops-header"><h3>Magasins de location de ski</h3></div>' +
        '<div class="sq-shops-empty">La liste des magasins partenaires pour cette station sera bient&ocirc;t disponible.</div>';
      return;
    }

    var stationName = station.name || slug;
    var baseShops = station.shops.map(function (s) {
      var match = findAlpyMatch(s, alpyShops);
      return Object.assign({}, s, {
        discount: match ? match.discount : 0
      });
    });

    container.innerHTML =
      '<div class="sq-shops-header">' +
        '<h3>Magasins de location de ski &agrave; ' + escapeHtml(stationName) +
          '<span class="sq-shops-count">' + baseShops.length + '</span></h3>' +
      '</div>' +
      '<div class="sq-shops-map" id="sq-map-' + slug + '"></div>' +
      '<div class="sq-shops-list" id="sq-list-' + slug + '"></div>';

    var map = L.map('sq-map-' + slug, {
      scrollWheelZoom: false,
      zoomControl: true
    }).setView(station.center, station.zoom || 14);

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
      maxZoom: 19
    }).addTo(map);

    map.on('click', function () { map.scrollWheelZoom.enable(); });
    map.on('mouseout', function () { map.scrollWheelZoom.disable(); });

    var markers = {};
    var shopsState = baseShops.slice();
    var listEl = document.getElementById('sq-list-' + slug);

    function buildMarkers() {
      Object.keys(markers).forEach(function (k) { map.removeLayer(markers[k].marker); });
      markers = {};
      shopsState.forEach(function (shop, idx) {
        var num = idx + 1;
        var icon = L.divIcon({
          className: 'sq-marker',
          html: '<span>' + num + '</span>',
          iconSize: [30, 30],
          iconAnchor: [15, 30],
          popupAnchor: [0, -28]
        });
        var marker = L.marker([shop.lat, shop.lng], { icon: icon }).addTo(map);
        marker.bindPopup(popupHtml(shop, num));
        marker.on('click', function () { highlight(shop.id); });
        markers[shop.id] = { marker: marker, num: num };
      });
    }

    function renderList() {
      listEl.innerHTML = '';
      shopsState.forEach(function (shop, idx) {
        listEl.insertAdjacentHTML('beforeend', cardHtml(shop, idx + 1));
      });
    }

    buildMarkers();
    renderList();

    listEl.addEventListener('click', function (e) {
      var card = e.target.closest('.sq-shop-card');
      if (!card) return;
      if (e.target.closest('a, button.sq-btn')) return;
      var id = card.dataset.id;
      var entry = markers[id];
      if (!entry) return;
      map.flyTo(entry.marker.getLatLng(), 16, { duration: .6 });
      entry.marker.openPopup();
      highlight(id);
    });

    function highlight(id) {
      Array.prototype.forEach.call(container.querySelectorAll('.sq-shop-card'), function (c) {
        c.classList.toggle('is-active', c.dataset.id === id);
      });
      Object.keys(markers).forEach(function (key) {
        var el = markers[key].marker.getElement();
        if (el) el.classList.toggle('is-active', key === id);
      });
      var activeCard = container.querySelector('.sq-shop-card.is-active');
      if (activeCard) activeCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

  }

  function cardHtml(shop, num) {
    var phoneClean = (shop.phone || '').replace(/\s/g, '');
    return '' +
      '<article class="sq-shop-card" data-id="' + escapeAttr(shop.id) + '">' +
        '<div class="sq-shop-head">' +
          '<h4 class="sq-shop-name"><span class="sq-shop-num">' + num + '</span>' + escapeHtml(shop.name) + '</h4>' +
        '</div>' +
        (shop.discount > 0 ? '<div class="sq-shop-discount">&minus;' + shop.discount + '% sur Alpy.com</div>' : '') +
        (shop.address ? infoLine('pin', escapeHtml(shop.address)) : '') +
        (shop.phone ? infoLine('phone', '<a href="tel:' + escapeAttr(phoneClean) + '">' + escapeHtml(shop.phone) + '</a>') : '') +
        (shop.hours ? infoLine('clock', escapeHtml(shop.hours)) : '') +
        '<div class="sq-shop-actions">' +
          (shop.website ? '<a class="sq-btn sq-btn-primary" href="' + escapeAttr(shop.website) + '" target="_blank" rel="noopener">Voir les offres</a>' : '') +
          '<a class="sq-btn" href="https://www.google.com/maps/dir/?api=1&destination=' + shop.lat + ',' + shop.lng + '" target="_blank" rel="noopener">Itin&eacute;raire</a>' +
        '</div>' +
      '</article>';
  }

  function popupHtml(shop, num) {
    return '<div style="min-width:180px"><strong>' + num + '. ' + escapeHtml(shop.name) + '</strong>' +
      (shop.address ? '<br><small>' + escapeHtml(shop.address) + '</small>' : '') +
      (shop.phone ? '<br><a href="tel:' + escapeAttr(shop.phone.replace(/\s/g, '')) + '">' + escapeHtml(shop.phone) + '</a>' : '') +
      (shop.discount > 0 ? '<br><span style="display:inline-block;margin-top:4px;background:#ff3c00;color:#fff;padding:2px 8px;border-radius:999px;font-size:.75rem;font-weight:700">&minus;' + shop.discount + '% sur Alpy.com</span>' : '') +
      '</div>';
  }

  var ICONS = {
    pin: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s-7-7.5-7-12a7 7 0 0114 0c0 4.5-7 12-7 12z"/><circle cx="12" cy="9" r="2.5"/></svg>',
    phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 01-2.18 2 19.79 19.79 0 01-8.63-3.07 19.5 19.5 0 01-6-6A19.79 19.79 0 012.12 4.18 2 2 0 014.11 2h3a2 2 0 012 1.72c.13.96.36 1.9.7 2.81a2 2 0 01-.45 2.11L8.09 9.91a16 16 0 006 6l1.27-1.27a2 2 0 012.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0122 16.92z"/></svg>',
    clock: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>'
  };

  function infoLine(iconKey, content) {
    return '<div class="sq-shop-info">' + (ICONS[iconKey] || '') + '<span>' + content + '</span></div>';
  }

  function escapeHtml(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  function escapeAttr(str) { return escapeHtml(str); }

  function boot() {
    var nodes = document.querySelectorAll('[data-sq-shops], #sq-shops');
    Array.prototype.forEach.call(nodes, init);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
