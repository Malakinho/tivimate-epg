/* EPG public diagnostics; checks published status and the latest GitHub Actions run.
   No IPTV credentials or private EPG link paths are fetched. */
(function () {
  "use strict";
  const $ = id => document.getElementById(id);
  const count = new Intl.NumberFormat("de-DE");
  const dt = new Intl.DateTimeFormat("de-DE", {
    dateStyle: "medium", timeStyle: "short", timeZone: "Europe/Berlin"
  });
  const workflow = "https://github.com/Malakinho/tivimate-epg/actions/workflows/update.yml";
  const api = "https://api.github.com/repos/Malakinho/tivimate-epg/actions/workflows/update.yml/runs?branch=main&per_page=1";
  let loading = false;

  function stamp(value) {
    const date = new Date(value || "");
    return Number.isNaN(date.valueOf()) ? "Unbekannt" : dt.format(date) + " Uhr";
  }
  function value(id, number) {
    $(id).textContent = typeof number === "number" && Number.isFinite(number)
      ? count.format(number) : "–";
  }
  function badge(text, color) {
    const element = $("diagHealth");
    element.className = "pill " + color;
    element.textContent = text;
  }
  function note(message, level) {
    const li = document.createElement("li");
    li.className = level;
    li.textContent = message;
    return li;
  }
  function addCell(tr, text) {
    const td = document.createElement("td");
    td.textContent = String(text == null ? "–" : text);
    tr.appendChild(td);
    return td;
  }
  function sourceRows(sources) {
    const body = $("diagSources");
    body.replaceChildren();
    if (!Array.isArray(sources)) {
      const tr = document.createElement("tr");
      const td = document.createElement("td");
      td.colSpan = 5;
      td.textContent = "Quellenstatus für diesen EPG-Stand noch nicht verfügbar.";
      tr.appendChild(td);
      body.appendChild(tr);
      return;
    }
    for (const source of sources) {
      const tr = document.createElement("tr");
      addCell(tr, source.name || "Unbekannt");
      addCell(tr, source.market || "–");
      const td = document.createElement("td");
      const status = source.status;
      const labels = {ok:"Erreichbar",failed:"Fehler",empty:"Keine Programme"};
      const classes = {ok:"ok",failed:"no",empty:"placeholder"};
      const pill = document.createElement("span");
      pill.className = "pill " + (classes[status] || "pruefen");
      pill.textContent = labels[status] || "Unbekannt";
      td.appendChild(pill);
      tr.appendChild(td);
      addCell(tr, count.format(Number(source.channels) || 0));
      addCell(tr, count.format(Number(source.programmes) || 0));
      body.appendChild(tr);
    }
  }
  function reportDiagnostics(report, run, runFailed) {
    const alerts = [];
    let severe = false, warning = false;
    let published = false;
    if (!report || typeof report !== "object" || !report.updated_utc) {
      severe = true;
      alerts.push(note("Der veröffentlichte EPG-Status ist nicht abrufbar. Die aktuelle Datenqualität ist unbekannt.", "bad"));
      $("diagUpdated").textContent = "Letzte Veröffentlichung: unbekannt";
      ["diagSourceOk", "diagPromoted", "diagStatic", "diagUncertain"].forEach(id => value(id, null));
      $("diagSourceDetail").textContent = "Status nicht verfügbar";
      sourceRows(null);
    } else {
      const date = new Date(report.updated_utc);
      const hours = (Date.now() - date.getTime()) / 3600000;
      published = Number.isFinite(hours) && hours >= 0;
      $("diagUpdated").textContent = "Letzter veröffentlichter EPG-Build: " + stamp(report.updated_utc);
      if (!published) {
        warning = true;
        alerts.push(note("Der Zeitstempel des EPG-Builds ist ungültig. Datenalter nicht verifizierbar.", "warn"));
      } else if (hours >= 6) {
        severe = true;
        alerts.push(note("EPG-Daten veraltet: letzte Aktualisierung vor " +
          Math.floor(hours) + " Stunden. Geplant ist alle 2 Stunden ein neuer Build.", "bad"));
      } else if (hours >= 3) {
        warning = true;
        alerts.push(note("Die letzte EPG-Aktualisierung liegt über 3 Stunden zurück. GitHub-Durchlauf prüfen.", "warn"));
      }
      const sources = Array.isArray(report.source_health) ? report.source_health : null;
      sourceRows(sources);
      const totalSources = sources ? sources.length : null;
      const liveSources = sources ? sources.filter(x => x.status === "ok").length : null;
      value("diagSourceOk", liveSources);
      $("diagSourceDetail").textContent = totalSources == null ? "Quellendaten fehlen"
        : "von " + count.format(totalSources) + " geprüften Quellen";
      value("diagPromoted", Number.isInteger(report.real_programme_placeholder_channels)
        ? report.real_programme_placeholder_channels : null);
      const staticNamed = Math.max(0,
        (Number(report.named_category_placeholder_channels) || 0) -
        (Number(report.named_category_live_channels) || 0));
      // Historical 24/7 fallback is reported only when the build publishes that metric.
      const staticAlways = Number(report.always_on_static_fallback_channels);
      value("diagStatic", Number.isFinite(staticAlways) ? staticNamed + staticAlways : null);
      const counts = report.dashboard_counts || {};
      value("diagUncertain", typeof counts.pruefen === "number" ? counts.pruefen : null);
      if (!sources) {
        warning = true;
        alerts.push(note("Die veröffentlichte Version enthält noch keine Detaildaten zu einzelnen EPG-Quellen.", "warn"));
      } else {
        const badSources = sources.filter(x => x.status !== "ok");
        if (badSources.length) {
          warning = true;
          alerts.push(note(badSources.length + " von " + sources.length +
            " EPG-Quellen waren beim letzten Build nicht vollständig verfügbar. Betroffen: " +
            badSources.map(x => x.name).join(", "), "warn"));
        }
      }
      if (typeof counts.pruefen === "number" && counts.pruefen > 0) {
        warning = true;
        alerts.push(note(count.format(counts.pruefen) +
          " Sender benötigen eine Prüfung ihrer EPG-Zuordnung.", "warn"));
      }
    }

    if (run) {
      const url = String(run.html_url || "");
      const safe = /^https:\/\/github\.com\/Malakinho\/tivimate-epg\/actions\/runs\/[0-9]+$/.test(url);
      $("diagRunLink").href = safe ? url : workflow;
      $("diagRunLink").textContent = "GitHub-Durchlauf #" + (run.run_number || "–");
      if (runFailed) {
        severe = true;
        alerts.unshift(note("Die letzte GitHub-Aktualisierung ist fehlgeschlagen (" +
          String(run.conclusion || "Fehler") + "). Die Webseite kann weiterhin ältere, zuvor veröffentlichte Daten anzeigen.", "bad"));
      } else if (run.status !== "completed") {
        alerts.push(note("Eine neue EPG-Aktualisierung läuft oder wartet auf GitHub. Der veröffentlichte Stand bleibt bis zum Erfolg gültig.", "good"));
      }
    } else {
      $("diagRunLink").href = workflow;
      $("diagRunLink").textContent = "GitHub-Aktualisierungen ansehen";
      warning = true;
      alerts.push(note("GitHub Actions konnte nicht live geprüft werden (z. B. API-Limit oder Verbindungsfehler). Der veröffentlichte EPG-Stand wird trotzdem angezeigt.", "warn"));
    }
    if (!alerts.length) alerts.push(note("Keine Probleme erkannt: EPG-Daten aktuell, Quellen verfügbar und letzter GitHub-Durchlauf erfolgreich.", "good"));
    $("diagAlerts").replaceChildren(...alerts);
    badge(severe ? "● Fehler erkannt" : warning ? "● Prüfung empfohlen" :
      published ? "● EPG aktuell" : "● Status unklar",
      severe ? "no" : warning ? "placeholder" : published ? "ok" : "pruefen");
  }
  async function reload() {
    if (loading) return;
    loading = true;
    $("diagRefresh").disabled = true;
    badge("◷ Prüfe Quellen und GitHub …", "pruefen");
    const now = Date.now();
    const [status, github] = await Promise.allSettled([
      fetch("status.json?ts=" + now, {cache:"no-store"}).then(r => {
        if (!r.ok) throw new Error("Status HTTP " + r.status);
        return r.json();
      }),
      fetch(api + "&ts=" + now, {
        cache:"no-store", headers:{"Accept":"application/vnd.github+json"}
      }).then(r => {
        if (!r.ok) throw new Error("GitHub HTTP " + r.status);
        return r.json();
      })
    ]);
    const report = status.status === "fulfilled" ? status.value : null;
    const run = github.status === "fulfilled"
      && Array.isArray(github.value.workflow_runs) ? github.value.workflow_runs[0] : null;
    const failed = Boolean(run && run.status === "completed"
      && ["failure", "timed_out", "cancelled", "action_required"].includes(run.conclusion));
    reportDiagnostics(report, run, failed);
    $("diagRefresh").disabled = false;
    loading = false;
  }
  $("diagRefresh").addEventListener("click", reload);
  reload();
})();
