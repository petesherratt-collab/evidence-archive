/* Read-only public browser. Archive-derived values are rendered with
 * textContent/createTextNode or safe links; retained bytes are never executed. */
(() => {
  "use strict";

  const ROOT = "./public/evidence";
  const BI = window.EvidenceBI;
  const state = {
    manifest: null, sources: [], entities: [], records: [], changes: [], observations: {}
  };

  const $ = (selector, root = document) => root.querySelector(selector);
  const text = value => document.createTextNode(value == null ? "" : String(value));
  const el = (tag, className, children = []) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    for (const child of children) {
      if (child && child.nodeType) node.append(child);
      else if (child != null) node.append(text(child));
    }
    return node;
  };
  const valueOrDash = value => value == null || value === "" ? "—" : value;
  const safeHref = value => {
    try {
      const url = new URL(value);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch (_) { return null; }
  };
  const link = (label, href, className = "") => {
    const node = el("a", className, [valueOrDash(label)]);
    const safe = safeHref(href) || (typeof href === "string" && href.startsWith("./") ? href : null);
    if (safe) node.href = safe;
    return node;
  };
  const formatTime = value => {
    if (!value || !Number.isFinite(Date.parse(value))) return "Unknown";
    return `${new Date(value).toLocaleString("en-GB", {
      timeZone: "UTC", dateStyle: "medium", timeStyle: "short"
    })} UTC`;
  };
  const formatOfficialDate = value => {
    if (!value || typeof value !== "string") return "Unknown";
    const datePart = value.slice(0, 10);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(datePart)) return "Unknown";
    const date = new Date(`${datePart}T00:00:00Z`);
    return Number.isFinite(date.getTime()) ? date.toLocaleDateString("en-GB", {
      timeZone: "UTC", dateStyle: "medium"
    }) : "Unknown";
  };
  const jsonText = value => value == null ? "—" :
    (typeof value === "string" ? value : JSON.stringify(value));
  const pill = (label, kind = "") => el("span", `pill ${kind}`, [label]);
  const dtdd = (label, value) => [el("dt", "", [label]), el("dd", "", Array.isArray(value) ? value : [value])];
  const tableCell = value => el("td", "", Array.isArray(value) ? value : [value]);
  const emptyRow = (colspan, message = "No matching records.") => {
    const row = el("tr");
    row.append(el("td", "muted", [message]));
    row.firstChild.colSpan = colspan;
    return row;
  };

  async function getJson(name) {
    const response = await fetch(`${ROOT}/${name}`, {cache: "no-store"});
    if (!response.ok) throw new Error(`Could not load ${name}`);
    return response.json();
  }

  async function loadBase() {
    const [manifest, sources, entities, records, changes] = await Promise.all([
      getJson("manifest.json"), getJson("sources.json"), getJson("entities.json"),
      getJson("records.json"), getJson("changes.json")
    ]);
    state.manifest = manifest;
    state.sources = sources.sources || [];
    state.entities = entities.entities || [];
    state.records = records.records || [];
    state.changes = changes.changes || [];
  }

  async function loadObservations(sourceId) {
    if (!state.observations[sourceId]) {
      state.observations[sourceId] = (await getJson(`observations/${sourceId}.json`)).observations || [];
    }
    return state.observations[sourceId];
  }

  function sourceById(id) { return state.sources.find(source => source.id === id); }
  function entityById(id) { return state.entities.find(entity => entity.id === id); }
  function recordById(id) { return state.records.find(record => record.id === id); }
  function sourceLink(source) {
    return source ? link(source.name, `./target.html?id=${encodeURIComponent(source.id)}`) : text("—");
  }
  function recordLink(record) {
    return record ? link(record.title || record.id, `./record.html?id=${encodeURIComponent(record.id)}`) : text("—");
  }
  function entityLink(entity) {
    return entity ? link(entity.name || entity.id, `./entity.html?id=${encodeURIComponent(entity.id)}`) : text("—");
  }
  function evidenceLink(observationId) {
    return observationId ? link("Evidence", `./evidence.html?id=${encodeURIComponent(observationId)}`) : text("—");
  }
  function isGovernmentSource(source) { return BI.isBusinessIntelligenceTarget(source); }
  function governmentSources() { return state.sources.filter(isGovernmentSource); }
  function controlSources() { return state.sources.filter(source => !isGovernmentSource(source)); }
  function governmentRecords() { return BI.businessRecords(state.records, state.sources); }
  function governmentActivity() { return BI.procurementActivityEvents(state.changes, state.sources); }
  function allGovernmentEvents() { return BI.businessEvents(state.changes, state.sources); }

  function coverageLabel(status) {
    const label = BI.timestampStatusText(status);
    return pill(label, status === "bitcoin-backed" ? "good" : status === "verification-failed" ? "bad" : "pending");
  }
  function chainLabel(status) { return pill(status === "sound" ? "Sound" : valueOrDash(status), status === "sound" ? "good" : "bad"); }
  function eventDescription(eventType) {
    return eventType === "first_seen" ? "New record observed" :
      eventType === "disappeared" ? "Record absent from later projection" :
      eventType === "raw_response_changed" ? "Raw response digest changed" :
      eventType === "content_changed" ? "Normalised document changed" :
      eventType === "value_changed" ? "Record value changed" :
      eventType === "date_changed" ? "Record date changed" :
      eventType === "supplier_changed" ? "Supplier changed" :
      eventType === "status_changed" ? "Record status changed" : valueOrDash(eventType);
  }
  function eventLabel(eventType) {
    return el("span", "event-label", [
      eventDescription(eventType), el("small", "event-code", [`(${eventType})`])
    ]);
  }
  function eventValue(change) {
    if (change.event_type === "first_seen") return "New deterministic record snapshot";
    if (change.event_type === "disappeared") return "Absent from later projection; not evidence of withdrawal";
    const comparison = change.comparison || {};
    if (comparison.mode === "raw_response" || comparison.mode === "normalised_document") {
      return `${String(comparison.old || "none").slice(0, 12)} → ${String(comparison.new || "none").slice(0, 12)}`;
    }
    if (comparison.field) return `${jsonText(comparison.old)} → ${jsonText(comparison.new)}`;
    return jsonText(comparison.new || comparison.old);
  }
  function recordParties(record, role) {
    return BI.partyEntries(record, role).map(party => entityLink(party));
  }
  function partyNames(record, role) {
    const names = BI.partyEntries(record, role).map(party => party.name || party.id).filter(Boolean);
    return names.length ? names.join(", ") : "—";
  }
  function recordValue(record) {
    const value = BI.recordValue(record);
    return value ? formatMoney(value.amount, value.currency) : "—";
  }
  function formatMoney(amount, currency) {
    try {
      return new Intl.NumberFormat("en-GB", {
        style: "currency", currency, currencyDisplay: "code", maximumFractionDigits: 2
      }).format(amount);
    } catch (_) {
      return `${currency} ${new Intl.NumberFormat("en-GB", {maximumFractionDigits: 2}).format(amount)}`;
    }
  }
  function valueSummary(aggregate) {
    return aggregate.currencies.map(item => formatMoney(item.total, item.currency));
  }
  function valueSummaryNode(aggregate) {
    const values = valueSummary(aggregate);
    return values.length ? el("div", "value-lines", values.map(value => el("span", "", [value]))) : text("Not supplied");
  }
  function metricCard(label, main, note) {
    return el("div", "card", [el("span", "", [label]), el("strong", "", [main]), el("small", "", [note])]);
  }
  function valueMetricCard(aggregate) {
    return el("div", "card value-card", [
      el("span", "", ["Observed award value"]),
      el("strong", "", [valueSummaryNode(aggregate)]),
      el("small", "", [`Usable numeric value for ${aggregate.valued_record_count} of ${aggregate.record_count} records; currencies kept separate`])
    ]);
  }
  function sortRecent(items, timeKey = "first_observed_at") {
    return items.slice().sort((a, b) =>
      (Date.parse(b[timeKey] || "") || 0) - (Date.parse(a[timeKey] || "") || 0) ||
      (b.id || "").localeCompare(a.id || ""));
  }
  function latestActivityForRecord(recordId, events) {
    return sortRecent(events.filter(event => event.record_id === recordId))[0] || null;
  }
  function sourceRecordActivity(sourceId, events = governmentActivity()) {
    return events.filter(event => event.target_id === sourceId);
  }
  function sourceLatestRecordActivity(sourceId, events = governmentActivity()) {
    return sourceRecordActivity(sourceId, events)[0] ?
      sortRecent(sourceRecordActivity(sourceId, events))[0].first_observed_at : null;
  }
  function officialDateNode(record) {
    const date = BI.officialDate(record);
    return date ? formatOfficialDate(date) : "Unknown";
  }
  function sourceNoticeLink(record) {
    const href = record?.source?.notice_url || record?.source?.url;
    return href && safeHref(href) ? link(record.source.notice_url ? "Source notice" : "Source", href) : text("—");
  }
  function renderError(error) {
    const main = $("main");
    if (main) main.append(el("p", "notice danger", [`Export could not be loaded: ${error.message}`]));
  }

  function renderIndex() {
    const records = governmentRecords();
    const activity = governmentActivity();
    const generated = state.manifest.generated_at;
    const values = BI.aggregateValues(records);
    const buyers = BI.entitiesForRecords(state.entities, records, "buyer").length;
    const suppliers = BI.entitiesForRecords(state.entities, records, "supplier").length;
    $("#summary-cards").replaceChildren(
      metricCard("Procurement records", records.length, "Distinct stable exported record IDs"),
      metricCard("Buyers", buyers, "Distinct stable buyer entity IDs"),
      metricCard("Suppliers", suppliers, "Distinct stable supplier entity IDs"),
      valueMetricCard(values)
    );
    [["activity-24", 1], ["activity-7", 7], ["activity-30", 30]].forEach(([id, days]) => {
      const count = BI.windowEvents(activity, generated, days).length;
      const node = $("#" + id);
      node.replaceChildren(text(count));
    });

    const feed = $("#recent-activity-rows");
    feed.replaceChildren();
    const recent = sortRecent(BI.windowEvents(activity, generated, 30)).slice(0, 12);
    if (!recent.length) feed.append(emptyRow(8, "No record activity in the last 30 days."));
    for (const event of recent) {
      const record = recordById(event.record_id);
      feed.append(el("tr", "", [
        tableCell(formatTime(event.first_observed_at)), tableCell(eventLabel(event.event_type)),
        tableCell(record ? recordLink(record) : event.title || "Unknown record"),
        tableCell(record ? (record.buyer ? entityLink(record.buyer) : "—") : "—"),
        tableCell(record ? (recordParties(record, "supplier").length ? recordParties(record, "supplier") : "—") : "—"),
        tableCell(record ? recordValue(record) : "—"), tableCell(sourceLink(sourceById(event.target_id))),
        tableCell(evidenceLink(event.observation_id))
      ]));
    }

    renderLargestAwards(records, activity, generated);
    renderRankedBuyers(records, activity);
    renderRankedSuppliers(records, activity);
    renderDirectAwards(records, activity, generated);
    renderSourceRows(activity);
    renderEvidenceStatus();
    renderControlRows();
    $("#generated").textContent = `Snapshot ${state.manifest.export_id}; generated from the latest retained observation at ${formatTime(generated)}. The browser is read-only and does not connect to the live archive.`;
  }

  function renderLargestAwards(records, activity, generated) {
    const host = $("#largest-awards");
    host.replaceChildren();
    const recent = BI.recentRecords(records, BI.windowEvents(activity, generated, 30), generated, Infinity)
      .map(record => ({record, value: BI.recordValue(record)})).filter(item => item.value);
    const grouped = new Map();
    for (const item of recent) {
      if (!grouped.has(item.value.currency)) grouped.set(item.value.currency, []);
      grouped.get(item.value.currency).push(item);
    }
    if (!grouped.size) {
      host.append(el("p", "muted", ["No recent records with a usable numeric award value."]));
      return;
    }
    for (const [currency, items] of [...grouped.entries()].sort(([a], [b]) => a.localeCompare(b))) {
      host.append(el("h3", "subheading", [`${currency} — ranked within currency`]), awardTable(items.sort((a, b) => b.value.amount - a.value.amount).slice(0, 5)));
    }
  }
  function awardTable(items) {
    const table = el("table");
    table.append(el("thead", "", [el("tr", "", ["Title", "Buyer", "Supplier", "Award value", "Award/publication date", "Source"].map(value => el("th", "", [value])))]));
    const body = el("tbody");
    for (const {record} of items) body.append(el("tr", "", [
      tableCell(recordLink(record)), tableCell(record.buyer ? entityLink(record.buyer) : "—"),
      tableCell(recordParties(record, "supplier").length ? recordParties(record, "supplier") : "—"),
      tableCell(recordValue(record)), tableCell(officialDateNode(record)), tableCell(sourceNoticeLink(record))
    ]));
    table.append(body);
    return table;
  }
  function renderRankedBuyers(records, activity) {
    const body = $("#buyer-rows");
    body.replaceChildren();
    const rows = BI.rankedEntities(records, state.entities, "buyer", activity).slice(0, 10);
    if (!rows.length) body.append(emptyRow(5, "No buyer records are available."));
    for (const row of rows) body.append(el("tr", "", [
      tableCell(entityLink(row.entity)), tableCell(row.procurement_count),
      tableCell(valueSummaryNode(row.observed_value)), tableCell(formatTime(row.latest_activity)),
      tableCell(row.counterparties_count)
    ]));
  }
  function renderRankedSuppliers(records, activity) {
    const body = $("#supplier-rows");
    body.replaceChildren();
    const rows = BI.rankedEntities(records, state.entities, "supplier", activity).slice(0, 10);
    if (!rows.length) body.append(emptyRow(5, "No supplier records are available."));
    for (const row of rows) body.append(el("tr", "", [
      tableCell(entityLink(row.entity)), tableCell(row.procurement_count),
      tableCell(row.counterparties_count), tableCell(valueSummaryNode(row.observed_value)),
      tableCell(formatTime(row.latest_activity))
    ]));
  }
  function renderDirectAwards(records, activity, generated) {
    const body = $("#direct-award-rows");
    body.replaceChildren();
    const direct = BI.recentRecords(records, BI.windowEvents(activity, generated, 30), generated, Infinity)
      .filter(record => record.source?.target_id === "uk-direct-awards-no-competition")
      .sort((a, b) => (Date.parse(BI.officialDate(b) || "") || 0) - (Date.parse(BI.officialDate(a) || "") || 0));
    if (!direct.length) body.append(emptyRow(6, "No direct awards with record activity in the last 30 days."));
    for (const record of direct.slice(0, 10)) body.append(el("tr", "", [
      tableCell(recordLink(record)), tableCell(record.buyer ? entityLink(record.buyer) : "—"),
      tableCell(recordParties(record, "supplier").length ? recordParties(record, "supplier") : "—"),
      tableCell(recordValue(record)), tableCell(officialDateNode(record)), tableCell(sourceNoticeLink(record))
    ]));
  }
  function renderSourceRows(activity) {
    const body = $("#source-rows");
    body.replaceChildren();
    for (const source of governmentSources()) body.append(el("tr", "", [
      tableCell(sourceLink(source)), tableCell(formatTime(source.last_observed_at)),
      tableCell(formatTime(sourceLatestRecordActivity(source.id, activity))),
      tableCell(source.record_count || 0), tableCell(sourceRecordActivity(source.id, activity).length),
      tableCell(coverageLabel(source.latest_timestamp_status))
    ]));
  }
  function renderEvidenceStatus() {
    const summary = state.manifest.summary || {};
    const coverage = summary.timestamp_coverage_counts || {};
    const sound = governmentSources().every(source => source.chain_status === "sound");
    $("#evidence-status-summary").replaceChildren(
      metricCard("Government observations", summary.government_observations || 0, "Retained observation attempts"),
      metricCard("Bitcoin-backed", coverage["bitcoin-backed"] || 0, "Independent timestamp evidence"),
      metricCard("Awaiting timestamp coverage", coverage.none || 0, "Pending is not Bitcoin-backed"),
      metricCard("Chain state", sound ? "Sound" : "Review", "Government target chains")
    );
  }
  function renderControlRows() {
    const body = $("#control-rows");
    body.replaceChildren();
    for (const source of controlSources()) body.append(el("tr", "", [
      tableCell(sourceLink(source)), tableCell(formatTime(source.last_observed_at)),
      tableCell(source.observation_count), tableCell(source.recorded_change_count),
      tableCell(chainLabel(source.chain_status)), tableCell(coverageLabel(source.latest_timestamp_status))
    ]));
  }

  function appendFilterOptions(select, values) {
    for (const [value, label] of values) {
      const option = el("option", "", [label]);
      option.value = value;
      select.append(option);
    }
  }
  function partyMatches(record, input, role) {
    if (!input) return true;
    const query = input.trim().toLocaleLowerCase();
    return BI.partyEntries(record, role).some(party =>
      `${party.name || ""} ${party.id || ""}`.toLocaleLowerCase().includes(query));
  }
  function renderChanges() {
    const sourceSelect = $("#filter-source");
    const typeSelect = $("#filter-type");
    appendFilterOptions(sourceSelect, state.sources.map(source => [source.id, source.name]));
    appendFilterOptions(typeSelect, [...new Set(state.changes.map(change => change.event_type))].sort().map(type => [type, type]));
    const buyers = state.entities.filter(entity => entity.role === "buyer");
    const suppliers = state.entities.filter(entity => entity.role === "supplier");
    for (const entity of buyers) {
      const option = el("option", "", [entity.name || entity.id]);
      option.value = entity.name || entity.id;
      $("#buyer-options").append(option);
    }
    for (const entity of suppliers) {
      const option = el("option", "", [entity.name || entity.id]);
      option.value = entity.name || entity.id;
      $("#supplier-options").append(option);
    }
    const params = new URLSearchParams(location.search);
    if (params.get("target")) sourceSelect.value = params.get("target");
    if (params.get("window")) $("#filter-window").value = params.get("window");
    const draw = () => {
      const windowValue = $("#filter-window").value;
      const days = windowValue === "24h" ? 1 : windowValue === "7d" ? 7 : windowValue === "30d" ? 30 : Infinity;
      const includeControl = $("#filter-include-control").checked;
      const base = includeControl ? state.changes : allGovernmentEvents();
      const filtered = BI.windowEvents(base, state.manifest.generated_at, days).filter(change =>
        (!sourceSelect.value || change.target_id === sourceSelect.value) &&
        (!typeSelect.value || change.event_type === typeSelect.value) &&
        partyMatches(recordById(change.record_id), $("#filter-buyer").value, "buyer") &&
        partyMatches(recordById(change.record_id), $("#filter-supplier").value, "supplier"));
      const body = $("#change-rows");
      body.replaceChildren();
      const sorted = sortRecent(filtered);
      if (!sorted.length) body.append(emptyRow(8, "No matching deterministic events."));
      for (const change of sorted) {
        const source = sourceById(change.target_id);
        const record = recordById(change.record_id);
        body.append(el("tr", "", [
          tableCell(formatTime(change.first_observed_at)), tableCell(eventLabel(change.event_type)),
          tableCell(record ? recordLink(record) : "Source-level evidence event"),
          tableCell(record?.buyer ? entityLink(record.buyer) : "—"),
          tableCell(record ? (recordParties(record, "supplier").length ? recordParties(record, "supplier") : "—") : "—"),
          tableCell(record ? recordValue(record) : eventValue(change)), tableCell(sourceLink(source)),
          tableCell(evidenceLink(change.observation_id))
        ]));
      }
      $("#change-count").textContent = `${sorted.length} deterministic event${sorted.length === 1 ? "" : "s"}`;
      $("#activity-scope").textContent = includeControl ?
        "Control included for operational review; it is excluded by default." :
        "Government procurement targets only; control excluded by default.";
    };
    ["#filter-window", "#filter-source", "#filter-type", "#filter-buyer", "#filter-supplier", "#filter-include-control"].forEach(selector => {
      $(selector).addEventListener("input", draw);
      $(selector).addEventListener("change", draw);
    });
    draw();
  }

  function renderContracts() {
    const body = $("#contract-rows");
    const search = $("#contract-search");
    const draw = () => {
      const query = search.value.trim().toLocaleLowerCase();
      const rows = governmentRecords().filter(record =>
        !query || [record.title, record.buyer?.name, partyNames(record, "supplier"), record.classification?.category]
          .filter(Boolean).join(" ").toLocaleLowerCase().includes(query));
      body.replaceChildren();
      if (!rows.length) body.append(emptyRow(7, "No matching procurement records."));
      for (const record of rows) body.append(el("tr", "", [
        tableCell(recordLink(record)), tableCell(record.buyer ? entityLink(record.buyer) : "—"),
        tableCell(recordParties(record, "supplier").length ? recordParties(record, "supplier") : "—"),
        tableCell(recordValue(record)), tableCell(officialDateNode(record)),
        tableCell(record.classification?.category || "—"), tableCell(sourceLink(sourceById(record.source.target_id)))
      ]));
      $("#contract-count").textContent = `${rows.length} procurement record${rows.length === 1 ? "" : "s"}`;
    };
    search.addEventListener("input", draw);
    draw();
  }

  function renderEntities() {
    const role = new URLSearchParams(location.search).get("role") === "supplier" ? "supplier" : "buyer";
    const records = governmentRecords();
    const activity = governmentActivity();
    const rows = BI.rankedEntities(records, state.entities, role, activity);
    $("#title").textContent = role === "buyer" ? "Buyers" : "Suppliers";
    $("#directory-kind").textContent = role === "buyer" ? "Government buyers linked to distinct procurement records." : "Suppliers linked to distinct government procurement records.";
    const search = $("#entity-search");
    const body = $("#entity-rows");
    const draw = () => {
      const query = search.value.trim().toLocaleLowerCase();
      const filtered = rows.filter(row => (row.entity.name || row.entity.id).toLocaleLowerCase().includes(query));
      body.replaceChildren();
      if (!filtered.length) body.append(emptyRow(5, "No matching entities."));
      for (const row of filtered) body.append(el("tr", "", [
        tableCell(entityLink(row.entity)), tableCell(row.procurement_count),
        tableCell(role === "buyer" ? row.counterparties_count : row.counterparties_count),
        tableCell(valueSummaryNode(row.observed_value)), tableCell(formatTime(row.latest_activity))
      ]));
      $("#entity-count").textContent = `${filtered.length} ${role} entit${filtered.length === 1 ? "y" : "ies"}`;
    };
    search.addEventListener("input", draw);
    draw();
  }

  async function renderTarget() {
    const id = new URLSearchParams(location.search).get("id");
    const source = sourceById(id) || state.sources[0];
    const targetEvents = state.changes.filter(change => change.target_id === source.id);
    const recordEvents = targetEvents.filter(change => change.record_id);
    $("#title").textContent = source.name;
    $("#source-kind").textContent = `${source.category} · ${source.source_type}`;
    $("#source-summary").replaceChildren(
      metricCard("Last observed", formatTime(source.last_observed_at), "Successful observation"),
      metricCard("Records", source.record_count || 0, "Stable structured records"),
      metricCard("Record activity", recordEvents.length, "Record-level archive events"),
      metricCard("Evidence", source.chain_status === "sound" ? "Sound" : "Review", BI.timestampStatusText(source.latest_timestamp_status))
    );
    const url = safeHref(source.url || source.configured_url);
    if (url) $("#source-url").replaceChildren(link(source.url || source.configured_url, url));
    else $("#source-url").textContent = "—";
    $("#source-metrics").replaceChildren(...[
      ...dtdd("First observed", formatTime(source.first_observed_at)),
      ...dtdd("Latest record activity", formatTime(sortRecent(recordEvents)[0]?.first_observed_at)),
      ...dtdd("Successful observations", source.successful_observation_count),
      ...dtdd("Deterministic events (excluding first_seen)", source.recorded_change_count),
      ...dtdd("Timestamp coverage", JSON.stringify(source.timestamp_coverage))
    ]);

    const records = state.records.filter(record => record.source?.target_id === source.id);
    const recordBody = $("#record-rows");
    recordBody.replaceChildren();
    if (!records.length) recordBody.append(emptyRow(6, "No structured procurement records are present for this source."));
    for (const record of records) recordBody.append(el("tr", "", [
      tableCell(recordLink(record)), tableCell(record.buyer ? entityLink(record.buyer) : "—"),
      tableCell(recordParties(record, "supplier").length ? recordParties(record, "supplier") : "—"),
      tableCell(recordValue(record)), tableCell(officialDateNode(record)), tableCell(record.status || "—")
    ]));

    const observations = await loadObservations(source.id);
    const observationBody = $("#observation-rows");
    observationBody.replaceChildren();
    for (const observation of observations.slice().reverse()) observationBody.append(el("tr", "", [
      tableCell(observation.sequence), tableCell(formatTime(observation.observed_at)),
      tableCell(observation.result.success ? "Success" : "Failed"),
      tableCell(observation.capture.changed === true ? "Yes" : observation.capture.changed === false ? "No" : "—"),
      tableCell(el("span", "hash", [observation.capture.content_sha256 || "Not retained"])),
      tableCell(chainLabel(observation.chain.status)), tableCell(coverageLabel(observation.anchor.status)),
      tableCell(evidenceLink(observation.id))
    ]));
  }

  async function renderRecord() {
    const id = new URLSearchParams(location.search).get("id");
    const record = recordById(id) || governmentRecords()[0];
    if (!record) throw new Error("No procurement record is available in this export");
    const source = sourceById(record.source.target_id);
    $("#title").textContent = record.title || record.id;
    $("#record-type").textContent = `${record.type || "Record"} · ${record.notice_type || "notice type not supplied"}`;
    const fields = $("#record-fields");
    fields.replaceChildren(...[
      ...dtdd("Buyer", record.buyer ? entityLink(record.buyer) : "—"),
      ...dtdd("Supplier", recordParties(record, "supplier").length ? recordParties(record, "supplier") : "—"),
      ...dtdd("Value", recordValue(record)),
      ...dtdd("CPV", record.classification?.cpv?.length ? record.classification.cpv.map(item => `${item.code} — ${item.description || ""}`) .join("; ") : "—"),
      ...dtdd("Category", record.classification?.category || "—"), ...dtdd("Status", record.status || "—"),
      ...dtdd("Published date", formatOfficialDate(record.dates?.published)),
      ...dtdd("Award date", formatOfficialDate(record.dates?.award)),
      ...dtdd("Contract start", formatOfficialDate(record.dates?.start)),
      ...dtdd("Contract end", formatOfficialDate(record.dates?.end)),
      ...dtdd("Source", source ? sourceLink(source) : "—"), ...dtdd("Source notice", sourceNoticeLink(record))
    ]);
    const changes = state.changes.filter(change => change.record_id === record.id).sort((a, b) =>
      (Date.parse(a.first_observed_at || "") || 0) - (Date.parse(b.first_observed_at || "") || 0));
    const changeBody = $("#record-change-rows");
    changeBody.replaceChildren();
    if (!changes.length) changeBody.append(emptyRow(4, "No record-level activity is retained for this record."));
    for (const change of changes) changeBody.append(el("tr", "", [
      tableCell(formatTime(change.first_observed_at)), tableCell(eventLabel(change.event_type)),
      tableCell(eventValue(change)), tableCell(evidenceLink(change.observation_id))
    ]));
    const observations = await loadObservations(record.source.target_id);
    const evidenceIds = new Set(record.evidence?.observation_ids || []);
    const evidenceObservations = observations.filter(item => evidenceIds.has(item.id)).sort((a, b) =>
      (Date.parse(b.observed_at || "") || 0) - (Date.parse(a.observed_at || "") || 0));
    const latest = evidenceObservations[0];
    const evidenceSummary = $("#evidence-summary");
    evidenceSummary.replaceChildren(...[
      ...dtdd("First observed", formatTime(record.dates?.first_observed)),
      ...dtdd("Last observed", formatTime(record.dates?.last_observed)),
      ...dtdd("First record activity observed", formatTime(record.dates?.first_observed_changed)),
      ...dtdd("Observation count", record.evidence?.observation_count || 0),
      ...dtdd("Observation IDs", evidenceObservations.length ? evidenceObservations.map(item => evidenceLink(item.id)) : "—")
    ]);
    if (latest) evidenceSummary.append(...[
      ...dtdd("Latest observed time", formatTime(latest.observed_at)),
      ...dtdd("Content SHA-256", el("span", "hash", [latest.capture.content_sha256 || "—"])),
      ...dtdd("Previous content SHA-256", el("span", "hash", [latest.capture.previous_content_sha256 || "—"])),
      ...dtdd("Chain state", chainLabel(latest.chain.status)),
      ...dtdd("Timestamp state", coverageLabel(latest.anchor.status)),
      ...dtdd("Latest evidence", evidenceLink(latest.id))
    ]);
  }

  async function renderEntity() {
    const id = new URLSearchParams(location.search).get("id");
    const entity = entityById(id) || state.entities[0];
    if (!entity) throw new Error("No entity is available in this export");
    const records = BI.recordsForEntity(governmentRecords(), entity.id, entity.role, state.entities);
    const activity = governmentActivity();
    const changes = activity.filter(change => records.some(record => record.id === change.record_id));
    const counterparties = new Map();
    const categories = new Set();
    for (const record of records) {
      for (const other of BI.partyEntries(record, entity.role === "buyer" ? "supplier" : "buyer")) if (other.id) counterparties.set(other.id, other);
      for (const category of [record.classification?.category].filter(Boolean)) categories.add(category);
    }
    $("#title").textContent = entity.name || entity.id;
    $("#entity-kind").textContent = `${entity.role} entity · stable identifier relationship view`;
    const values = BI.aggregateValues(records);
    const latest = sortRecent(changes)[0]?.first_observed_at;
    $("#entity-summary").replaceChildren(
      metricCard("Procurement records", records.length, "Distinct stable record IDs"),
      valueMetricCard(values), metricCard("Latest activity", formatTime(latest), "Record activity, not polling"),
      metricCard(entity.role === "buyer" ? "Suppliers" : "Buyers", counterparties.size, "Distinct linked entities")
    );
    $("#entity-fields").replaceChildren(...[
      ...dtdd("Entity ID", el("span", "hash", [entity.id])), ...dtdd("Role", entity.role),
      ...dtdd(entity.role === "buyer" ? "Suppliers" : "Buyers", [...counterparties.values()].sort((a, b) => (a.name || "").localeCompare(b.name || "")).map(item => entityLink(item))),
      ...dtdd("Categories", [...categories].sort().join(", ") || "—"),
      ...dtdd("Observed value coverage", `${values.valued_record_count} of ${values.record_count} records have a usable numeric value`)
    ]);
    const body = $("#entity-record-rows");
    body.replaceChildren();
    const ordered = records.slice().sort((a, b) =>
      (Date.parse(latestActivityForRecord(b.id, activity)?.first_observed_at || "") || 0) -
      (Date.parse(latestActivityForRecord(a.id, activity)?.first_observed_at || "") || 0));
    if (!ordered.length) body.append(emptyRow(6, "No procurement records are linked to this entity."));
    for (const record of ordered) body.append(el("tr", "", [
      tableCell(recordLink(record)), tableCell(sourceLink(sourceById(record.source.target_id))),
      tableCell(recordValue(record)), tableCell(officialDateNode(record)), tableCell(record.status || "—"),
      tableCell(formatTime(latestActivityForRecord(record.id, activity)?.first_observed_at))
    ]));
  }

  async function renderEvidence() {
    const id = new URLSearchParams(location.search).get("id");
    const [sourceId] = (id || "").split(":poll:");
    const observations = await loadObservations(sourceId);
    const observation = observations.find(item => item.id === id) || observations[0];
    if (!observation) throw new Error("No evidence observation is available for this link");
    const source = sourceById(observation.target_id);
    $("#title").textContent = `Observation ${observation.id}`;
    $("#evidence-source").replaceChildren(source ? sourceLink(source) : text(observation.target_id));
    const fields = $("#evidence-fields");
    fields.replaceChildren(...[
      ...dtdd("Observation ID", el("span", "hash", [observation.id])),
      ...dtdd("Observed time", formatTime(observation.observed_at)),
      ...dtdd("Source URL", safeHref(observation.source.url) ? link(observation.source.url, observation.source.url) : "—"),
      ...dtdd("HTTP status", valueOrDash(observation.result.http_status)),
      ...dtdd("Result", observation.result.success ? "Success" : `Failed: ${observation.result.error || "no error supplied"}`),
      ...dtdd("Content SHA-256", el("span", "hash", [observation.capture.content_sha256 || "—"])),
      ...dtdd("Previous content SHA-256", el("span", "hash", [observation.capture.previous_content_sha256 || "—"])),
      ...dtdd("Changed", observation.capture.changed == null ? "—" : observation.capture.changed ? "Yes" : "No"),
      ...dtdd("Chain sequence", observation.sequence), ...dtdd("Previous chain entry", el("span", "hash", [observation.chain.previous_entry_sha256 || "—"])),
      ...dtdd("Chain entry", el("span", "hash", [observation.chain.entry_sha256 || "—"])),
      ...dtdd("Chain head", el("span", "hash", [observation.chain.head_sha256 || "—"])),
      ...dtdd("Chain state", chainLabel(observation.chain.status)), ...dtdd("Timestamp state", coverageLabel(observation.anchor.status)),
      ...dtdd("Anchor manifest", observation.anchor.manifest_ref || "—"), ...dtdd("Anchor manifest SHA-256", el("span", "hash", [observation.anchor.manifest_sha256 || "—"]))
    ]);
    $("#timestamp-note").textContent = observation.anchor.status === "none" ?
      "This observation is awaiting timestamp coverage. That is a coverage state, not archive damage." :
      observation.anchor.status === "pending" ? "The proof is pending external Bitcoin attestation; it is not Bitcoin-backed." :
      observation.anchor.status === "verification-failed" ? "The retained timestamp evidence could not be verified; Bitcoin backing is not claimed." :
      "The archive records completed Bitcoin attestation coverage for this observation.";
  }

  async function main() {
    await loadBase();
    const page = document.body.dataset.page;
    if (page === "index") renderIndex();
    if (page === "changes") renderChanges();
    if (page === "contracts") renderContracts();
    if (page === "entities") renderEntities();
    if (page === "target") await renderTarget();
    if (page === "record") await renderRecord();
    if (page === "entity") await renderEntity();
    if (page === "evidence") await renderEvidence();
  }
  main().catch(renderError);
})();
