# Automatische PPV-Sendernamen (Xtream)

Die öffentliche XMLTV-URL bleibt:
https://malakinho.github.io/tivimate-epg/epg.xml.gz

## Einmalige Einrichtung

1. Öffne **Settings → Secrets and variables → Actions** im Repository.
2. Klicke **New repository secret**.
3. **Name:** `XTREAM_M3U_URL`
4. **Secret:** deinen vollständigen **eigenen** M3U-Link mit `username` und `password`. Nicht im Repository, in Issues oder im Chat veröffentlichen.
5. Klicke **Add secret**.
6. Öffne **Actions → EPG automatisch aktualisieren → Run workflow** und starte einmal den Workflow.

## Ergebnis

Bei jeder Aktualisierung (geplant alle zwei Stunden) wird die aktuelle Xtream-Livesenderliste privat abgerufen. Für Sender mit `PPV` im Namen wird der **exakte aktuelle Sendername** als ganztägiger Programmtitel geschrieben. Die interne EPG-Kennung wird aus der stabileren Xtream-Stream-ID abgeleitet und ändert sich bei reiner Umbenennung nicht.

Sender ohne `PPV` im Namen, aber in einer Kategorie namens PPV, werden mit dieser ersten Version nicht automatisch erkannt. Falls sich die Stream-ID beim Anbieter ändert, ändert sich auch die XMLTV-Kennung. Bei PPV-Sendern ohne bestehende `tvg-id` kann der Player weiterhin eine Zuordnung brauchen.

Fehlt das Secret, bleibt die bisherige statische PPV-Liste erhalten. Ist das Secret gesetzt und der Anbieterabruf scheitert, wird dieser Workflow abgebrochen; die zuvor veröffentlichte EPG bleibt erhalten. Die PPV-Zugangsdaten werden nicht in der öffentlichen EPG gespeichert.

**Hinweis:** Die automatische Namensaktualisierung funktioniert erst nach dem Einrichten des Secrets und einem erfolgreichen Workflow-Lauf. Die GitHub Actions-Ausführung kann gelegentlich verzögert starten.
