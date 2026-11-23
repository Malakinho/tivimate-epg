# Automatisch aktualisierter M3U-Link für iMPlayer

**Status:** Implementierung vorbereitet und getestet, aber noch **nicht live**.
Ohne einen privaten Server/Cloudflare-Worker dürfen wir die Stream-URLs
inklusive Zugangsdaten NICHT auf öffentlichen GitHub Pages veröffentlichen.

## Was passiert automatisch?

- Bei jedem Abruf des dauerhaften M3U-Links lädt der Worker die aktuelle
  Anbieter-M3U über UPSTREAM_M3U_URL neu.
- Er lädt die aktuellen EPG-Zuordnungen aus unserer EPG-Webapp
  (https://malakinho.github.io/tivimate-epg/dashboard.json, im Worker
  höchstens 5 Minuten im Arbeitsspeicher zwischengespeichert).
- Nur sichere Sender-IDs mit Status "programm" oder "platzhalter" werden
  in die bestehenden tvg-id-Felder eingefügt. Unklare Zuordnungen bleiben
  unverändert. Stream-Adressen werden nicht verändert.
- Die persönliche EPG-URL wird als x-tvg-url und url-tvg in den
  Playlist-Kopf geschrieben. iMPlayer kann die XMLTV-URL zusätzlich
  als EPG-Quelle benötigen.
- Es entsteht nie eine öffentliche M3U-Datei. Die große Playlist wird
  während des Abrufs als Stream verarbeitet.

Der Player muss **seine M3U-Playlist automatisch neu laden**. Das erledigt
der Worker nicht von sich aus, sondern bei jedem Abruf. Aktiviere daher
die automatischen Playlist- und EPG-Updates in iMPlayer.

## Einmalige Einrichtung bei Cloudflare

1. Eigene Cloudflare-Workers-Installation einrichten (Cloudflare-Konto nötig).
2. Im Ordner m3u_gateway mit Node.js und Wrangler anmelden:

       npx wrangler login

3. Diese drei vertraulichen Werte nacheinander als Worker-Secrets setzen.
   **Werte niemals in GitHub, Chat oder eine öffentliche Webseite schreiben.**

       npx wrangler secret put UPSTREAM_M3U_URL
       npx wrangler secret put EPG_URL
       npx wrangler secret put ACCESS_TOKEN

   UPSTREAM_M3U_URL = kompletter M3U-Link deines IPTV-Anbieters.
   EPG_URL = dein persönlicher XMLTV-Link aus der EPG-Webapp (HTTPS).
   ACCESS_TOKEN = langes, zufälliges URL-sicheres Token (mind. 32 Zeichen).

   Ein sicheres Token lässt sich lokal erzeugen mit:

       python -c "import secrets; print(secrets.token_urlsafe(36))"

4. Gateway bereitstellen:

       npx wrangler deploy

5. Deine dauerhaft gleichbleibende Playlist-URL sieht so aus:

       https://DEIN-WORKER.workers.dev/m3u/DEIN-ACCESS-TOKEN/playlist.m3u

6. In iMPlayer als **M3U-Playlist per URL** einfügen. Deinen persönlichen
   EPG-Link als externe XMLTV-Quelle eintragen (falls die App ihn nicht
   aus dem M3U-Kopf übernimmt). Automatische Playlist- und EPG-Updates
   aktivieren.

## Sicherheit und Grenzen

- Der Link ist mit einem **Bearer-Token** geschützt, nicht per Login
  oder IP-Adresse. Wer den Link kennt, kann die enthaltenen Streams abrufen.
  Bei unerwünschter Weitergabe das Token über Cloudflare rotieren.
- Die Anbieter-M3U kann Zugangsdaten in Stream-URLs enthalten. Deshalb
  niemals auf GitHub Pages, GitHub Artifacts oder in öffentlichen Dateien
  ablegen. Cloudflare verarbeitet sie nur beim autorisierten Abruf.
- Response-Header verbieten Caching; öffentliche EPG-Namenszuordnungen
  werden maximal fünf Minuten pro aktivem Worker-Prozess gespeichert.
- Wenn der Anbieter Cloudflare-Rechenzentren blockiert, funktioniert der
  Abruf ggf. nicht. Dann wird ein anderes privates Hosting benötigt.
- Große M3U-Dateien können auf dem kostenlosen Workers-CPU-Kontingent
  scheitern; ggf. Workers Paid oder ein kleiner privater Server nötig.
- Der Dienst ist **kein Xtream-Server** und kein Video-Proxy. Er übergibt
  die Streams genau so, wie der IPTV-Anbieter sie liefert.
- Die öffentliche EPG-Webapp und ihre Freundes-EPG-Links sind davon
  unabhängig. Der neue M3U-Link wird erst mit eigenem Hosting nutzbar.

## Tests

Vom Repository-Root ohne Zugangsdaten ausführbar:

    node --test m3u_gateway/test.mjs

Die Tests prüfen Zugangsprüfung, Magenta Sport vs. League, DAZN BAR,
mehrdeutige Sender, zeilenweises Streaming und unveränderte Stream-URLs.
