/* Read-only public browser. All archive-derived values are assigned through
 * textContent or safe hrefs; archived bytes are never fetched or executed. */
(() => {
  "use strict";

  const ROOT = "./public/evidence";
  const state = { manifest: null, sources: [], entities: [], records: [], changes: [], observations: {} };

  const $ = (selector, root = document) => root.querySelector(selector);
  const text = value => document.createTextNode(value == null ? "" : String(value));
  const el = (tag, className, children = []) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    for (const child of children) node.append(child && child.nodeType ? child : text(child));
    return node;
  };
  const safeHref = value => {
    try {
      const url = new URL(value);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch (_) { return null; }
  };
  const link = (label, href, className = "") => {
    const node = el("a", className, [label]);
    const safe = safeHref(href) || (href && href.startsWith("./") ? href : null);
    if (safe) node.href = safe;
    return node;
  };
  const formatTime = value => value ? `${new Date(value).toLocaleString("en-GB", {
    timeZone: "UTC", dateStyle: "medium", timeStyle: "short"
  })} UTC` : "Not available";
  const isoTime = value => value || "";
  const jsonText = value => value == null ? "Not available" :
    (typeof value === "string" ? value : JSON.stringify(value));
  const pill = (label, kind = "") => el("span", `pill ${kind}`, [label]);
  const dtdd = (label, value) => [el("dt", "", [label]), el("dd", "", Array.isArray(value) ? value : [value])];
  const tableCell = value => el("td", "", [value]);

  async function getJson(name) {
    const response = await fetch(`${ROOT}/${name}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`Could not load ${name}`);
    return response.json();
  }

  async function loadBase() {
    const [manifest, sources, entities, records, changes] = await Promise.all([
      getJson("manifest.json"), getJson("sources.json"),
      getJson("entities.json"), getJson("records.json"), getJson("changes.json")
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
  function sourceLink(source) { return link(source.name, `./target.html?id=${encodeURIComponent(source.id)}`); }
  function recordLink(record) { return link(record.title || record.id, `./record.html?id=${encodeURIComponent(record.id)}`); }
  function entityLink(entity) { return link(entity.name || entity.id, `./entity.html?id=${encodeURIComponent(entity.id)}`); }
  function changeKind(eventType) {
    return eventType === "raw_response_changed" ? "Raw response changed" :
      eventType === "content_changed" ? "Structured/document content changed" :
      eventType.replaceAll("_", " ");
  }
  function coverageLabel(status) {
    return status === "bitcoin-backed" ? pill("Bitcoin-backed", "good") :
      status === "pending" ? pill("Pending", "pending") :
      status === "verification-failed" ? pill("Verification failed", "bad") :
      pill("Awaiting coverage", "pending");
  }
  function chainLabel(status) { return pill(status === "sound" ? "Sound" : status, status === "sound" ? "good" : "bad"); }
  function eventValue(change) {
    const comparison = change.comparison || {};
    if (comparison.mode === "raw_response") return `${comparison.old || "none"} → ${comparison.new || "none"}`;
    if (comparison.field) return `${jsonText(comparison.old)} → ${jsonText(comparison.new)}`;
    return jsonText(comparison.new || comparison.old);
  }

  function renderError(error) {
    const main = $("main");
    if (main) main.append(el("p", "notice danger", [`Export could not be loaded: ${error.message}`]));
  }

  function renderIndex() {
    const government = state.sources.filter(source => source.kind !== "control");
    const controls = state.sources.filter(source => source.kind === "control");
    const generated = state.manifest.generated_at;
    const recent = age => state.changes.filter(change => change.event_type !== "first_seen" &&
      new Date(generated) - new Date(change.first_observed_at) <= age).length;
    const coverage = state.manifest.summary.timestamp_coverage_counts || {};
    const cards = $("#summary-cards");
    [["Sources monitored", government.length, "government sources"],
      ["Observations", government.reduce((sum, source) => sum + source.observation_count, 0), "successful and failed attempts"],
      ["Recorded changes", government.reduce((sum, source) => sum + source.recorded_change_count, 0), "deterministic events"],
      ["Timestamp coverage", coverage["bitcoin-backed"] || 0, `${coverage.none || 0} awaiting coverage`]
    ].forEach(([label, value, note]) => cards.append(el("div", "card", [
      el("span", "", [label]), el("strong", "", [value]), el("small", "", [note])
    ])));
    $("#activity-24").textContent = recent(24 * 60 * 60 * 1000);
    $("#activity-7").textContent = recent(7 * 24 * 60 * 60 * 1000);
    $("#activity-30").textContent = recent(30 * 24 * 60 * 60 * 1000);

    const body = $("#source-rows");
    for (const source of government) {
      const latest = source.latest_recorded_change_at ? formatTime(source.latest_recorded_change_at) : "None recorded";
      const row = el("tr");
      row.append(tableCell(sourceLink(source)), tableCell(source.category),
        tableCell(formatTime(source.last_observed_at)), tableCell(latest),
        tableCell(source.observation_count), tableCell(source.recorded_change_count),
        tableCell(chainLabel(source.chain_status)), tableCell(coverageLabel(source.latest_timestamp_status)));
      body.append(row);
    }
    const controlBody = $("#control-rows");
    for (const source of controls) {
      const row = el("tr");
      row.append(tableCell(sourceLink(source)), tableCell(formatTime(source.last_observed_at)),
        tableCell(source.observation_count), tableCell(source.recorded_change_count),
        tableCell(chainLabel(source.chain_status)), tableCell(coverageLabel(source.latest_timestamp_status)));
      controlBody.append(row);
    }
    $("#generated").textContent = `Snapshot ${state.manifest.export_id}; generated from the latest retained observation at ${formatTime(generated)}. The browser is read-only and does not connect to the live archive.`;
  }

  function renderChanges() {
    const sourceSelect = $("#filter-source");
    const typeSelect = $("#filter-type");
    for (const source of state.sources) {
      const option = el("option", "", [source.name]);
      option.value = source.id;
      sourceSelect.append(option);
    }
    const types = [...new Set(state.changes.map(change => change.event_type))].sort();
    for (const type of types) {
      const option = el("option", "", [changeKind(type)]);
      option.value = type;
      typeSelect.append(option);
    }
    const params = new URLSearchParams(location.search);
    if (params.get("target")) sourceSelect.value = params.get("target");
    if (params.get("window")) $("#filter-window").value = params.get("window");
    const draw = () => {
      const windowValue = $("#filter-window").value;
      const age = windowValue === "24h" ? 24 * 60 * 60 * 1000 : windowValue === "7d" ? 7 * 86400000 : windowValue === "30d" ? 30 * 86400000 : Infinity;
      const generated = new Date(state.manifest.generated_at);
      const filtered = state.changes.filter(change =>
        generated - new Date(change.first_observed_at) <= age &&
        (!sourceSelect.value || change.target_id === sourceSelect.value) &&
        (!typeSelect.value || change.event_type === typeSelect.value));
      const body = $("#change-rows"); body.replaceChildren();
      for (const change of filtered) {
        const source = sourceById(change.target_id);
        const record = change.record_id ? recordById(change.record_id) : null;
        const evidenceHref = `./evidence.html?id=${encodeURIComponent(change.observation_id)}`;
        const row = el("tr");
        row.append(tableCell(formatTime(change.first_observed_at)),
          tableCell(source ? sourceLink(source) : change.target_id),
          tableCell(record ? recordLink(record) : (change.title || "Source-level event")),
          tableCell(pill(changeKind(change.event_type), change.comparison.mode === "raw_response" ? "pending" : "")),
          tableCell(eventValue(change)), tableCell(link("Evidence", evidenceHref)));
        body.append(row);
      }
      $("#change-count").textContent = `${filtered.length} event${filtered.length === 1 ? "" : "s"}`;
    };
    ["#filter-window", "#filter-source", "#filter-type"].forEach(selector => $(selector).addEventListener("change", draw));
    draw();
  }

  async function renderTarget() {
    const id = new URLSearchParams(location.search).get("id");
    const source = sourceById(id) || state.sources[0];
    $("#title").textContent = source.name;
    $("#source-kind").textContent = `${source.category} · ${source.source_type}`;
    $("#source-summary").replaceChildren(
      el("div", "card", [el("span", "", ["Last observed"]), el("strong", "", [formatTime(source.last_observed_at)])]),
      el("div", "card", [el("span", "", ["Observations"]), el("strong", "", [source.observation_count])]),
      el("div", "card", [el("span", "", ["Recorded changes"]), el("strong", "", [source.recorded_change_count])]),
      el("div", "card", [el("span", "", ["Chain"]), el("strong", "", [source.chain_status])])
    );
    const url = safeHref(source.url || source.configured_url);
    if (url) $("#source-url").append(link(source.url || source.configured_url, url)); else $("#source-url").textContent = source.url || "Not available";
    $("#source-metrics").replaceChildren(...[
      ...dtdd("First observed", formatTime(source.first_observed_at)),
      ...dtdd("First observed changed / latest recorded change", formatTime(source.latest_recorded_change_at)),
      ...dtdd("Successful observations", source.successful_observation_count),
      ...dtdd("Records exposed", source.record_count),
      ...dtdd("Timestamp coverage", JSON.stringify(source.timestamp_coverage))
    ]);
    const observations = await loadObservations(source.id);
    const body = $("#observation-rows");
    for (const observation of observations.slice().reverse()) {
      const row = el("tr");
      row.append(tableCell(observation.sequence), tableCell(formatTime(observation.observed_at)),
        tableCell(observation.result.success ? "Success" : "Failed"),
        tableCell(observation.capture.changed === true ? "Yes" : observation.capture.changed === false ? "No" : "—"),
        tableCell(observation.capture.content_sha256 || "Not retained"),
        tableCell(chainLabel(observation.chain.status)), tableCell(coverageLabel(observation.anchor.status)),
        tableCell(link("Evidence", `./evidence.html?id=${encodeURIComponent(observation.id)}`)));
      body.append(row);
    }
    const records = state.records.filter(record => record.source.target_id === source.id);
    const recordBody = $("#record-rows");
    for (const record of records) {
      const row = el("tr");
      row.append(tableCell(recordLink(record)), tableCell(record.buyer ? entityLink(record.buyer) : "Not available"),
        tableCell(record.supplier ? entityLink(record.supplier) : record.suppliers?.map(item => item.name).join(", ") || "Not available"), tableCell(record.status || "Not available"),
        tableCell(record.value ? jsonText(record.value) : "Not available"));
      recordBody.append(row);
    }
  }

  async function renderRecord() {
    const id = new URLSearchParams(location.search).get("id");
    const record = recordById(id) || state.records[0];
    $("#title").textContent = record.title || record.id;
    $("#record-type").textContent = `${record.type} · ${record.notice_type || "notice type not supplied"}`;
    const fields = $("#record-fields");
    const values = [
      ["Buyer", record.buyer ? entityLink(record.buyer) : null], ["Supplier", record.supplier ? entityLink(record.supplier) : record.suppliers?.map(item => item.name).join(", ")],
      ["Value", record.value && jsonText(record.value)], ["CPV", record.classification.cpv.map(item => `${item.code} — ${item.description || ""}`).join("; ")],
      ["Category", record.classification.category], ["Status", record.status], ["Published", record.dates.published], ["Award", record.dates.award],
      ["Contract start", record.dates.start], ["Contract end", record.dates.end], ["Source URL", record.source.url]
    ];
    for (const [label, value] of values) fields.append(...dtdd(label, value || "Not available"));
    const sourceUrl = safeHref(record.source.notice_url || record.source.url);
    if (sourceUrl) fields.lastChild.replaceChildren(link(sourceUrl, sourceUrl));
    $("#evidence-summary").replaceChildren(...[
      ...dtdd("First observed", formatTime(record.dates.first_observed)),
      ...dtdd("Last observed", formatTime(record.dates.last_observed)),
      ...dtdd("First observed changed", formatTime(record.dates.first_observed_changed)),
      ...dtdd("Observation count", record.evidence.observation_count)
    ]);
    const changes = state.changes.filter(change => change.record_id === record.id);
    const body = $("#record-change-rows");
    for (const change of changes) {
      const row = el("tr");
      row.append(tableCell(formatTime(change.first_observed_at)), tableCell(changeKind(change.event_type)), tableCell(eventValue(change)), tableCell(link("Evidence", `./evidence.html?id=${encodeURIComponent(change.observation_id)}`)));
      body.append(row);
    }
    const observations = await loadObservations(record.source.target_id);
    const evidenceIds = new Set(record.evidence.observation_ids || []);
    const evidenceObservations = observations.filter(item => evidenceIds.has(item.id));
    const latestEvidence = evidenceObservations[evidenceObservations.length - 1];
    if (latestEvidence) {
      $("#evidence-summary").append(
        ...dtdd("Content SHA-256", latestEvidence.capture.content_sha256),
        ...dtdd("Previous content SHA-256", latestEvidence.capture.previous_content_sha256),
        ...dtdd("Chain state", latestEvidence.chain.status),
        ...dtdd("Timestamp state", latestEvidence.anchor.status),
        ...dtdd("Latest evidence", link("Observation", `./evidence.html?id=${encodeURIComponent(latestEvidence.id)}`))
      );
    }
  }

  async function renderEntity() {
    const id = new URLSearchParams(location.search).get("id");
    const entity = entityById(id) || state.entities[0];
    if (!entity) throw new Error("No entity is available in this export");
    const records = state.records.filter(record =>
      record.buyer?.id === entity.id || record.suppliers?.some(item => item.id === entity.id));
    const recordIds = new Set(records.map(record => record.id));
    const changes = state.changes.filter(change => recordIds.has(change.record_id));
    const observationIds = new Set(records.flatMap(record => record.evidence.observation_ids || []));
    const counterparties = new Map();
    const categories = new Set();
    for (const record of records) {
      const others = entity.role === "buyer" ? record.suppliers : [record.buyer];
      for (const other of others || []) if (other) counterparties.set(other.id, other);
      if (record.classification.category) categories.add(record.classification.category);
    }
    $("#title").textContent = entity.name || entity.id;
    $("#entity-kind").textContent = `${entity.role} entity · deterministic relationship view`;
    $("#entity-summary").replaceChildren(
      el("div", "card", [el("span", "", ["Records exposed"]), el("strong", "", [records.length])]),
      el("div", "card", [el("span", "", ["Observations linked"]), el("strong", "", [observationIds.size])]),
      el("div", "card", [el("span", "", ["Recorded changes"]), el("strong", "", [changes.filter(item => item.event_type !== "first_seen").length])]),
      el("div", "card", [el("span", "", ["Role"]), el("strong", "", [entity.role])])
    );
    $("#entity-fields").replaceChildren(
      ...dtdd("Entity ID", entity.id), ...dtdd("Role", entity.role),
      ...dtdd(entity.role === "buyer" ? "Suppliers" : "Buyers",
        [...counterparties.values()].map(item => entityLink(item))),
      ...dtdd("Categories", [...categories].sort().join(", ") || "Not available")
    );
    const body = $("#entity-record-rows");
    for (const record of records) {
      const row = el("tr");
      row.append(tableCell(recordLink(record)), tableCell(sourceLink(sourceById(record.source.target_id))),
        tableCell(record.status || "Not available"), tableCell(record.value ? jsonText(record.value) : "Not available"));
      body.append(row);
    }
  }

  async function renderEvidence() {
    const id = new URLSearchParams(location.search).get("id");
    const [sourceId] = (id || "").split(":poll:");
    const observations = await loadObservations(sourceId);
    const observation = observations.find(item => item.id === id) || observations[0];
    const source = sourceById(observation.target_id);
    $("#title").textContent = `Observation ${observation.id}`;
    $("#evidence-source").append(source ? sourceLink(source) : observation.target_id);
    const fields = $("#evidence-fields");
    const rows = [
      ["Observation ID", observation.id], ["Observed", formatTime(observation.observed_at)], ["Source URL", observation.source.url],
      ["HTTP status", observation.result.http_status], ["Result", observation.result.success ? "Success" : `Failed: ${observation.result.error || "no error supplied"}`],
      ["Content SHA-256", observation.capture.content_sha256], ["Previous content SHA-256", observation.capture.previous_content_sha256],
      ["Changed", observation.capture.changed == null ? "Not applicable" : observation.capture.changed ? "Yes" : "No"],
      ["Chain sequence", observation.sequence], ["Previous chain entry", observation.chain.previous_entry_sha256],
      ["Chain entry", observation.chain.entry_sha256], ["Chain head", observation.chain.head_sha256],
      ["Chain state", observation.chain.status], ["Timestamp state", observation.anchor.status],
      ["Anchor manifest", observation.anchor.manifest_ref], ["Anchor manifest SHA-256", observation.anchor.manifest_sha256]
    ];
    for (const [label, value] of rows) fields.append(...dtdd(label, value || "Not available"));
    const linkNode = safeHref(observation.source.url);
    if (linkNode) fields.lastChild.replaceChildren(link(observation.source.url, linkNode));
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
    if (page === "target") await renderTarget();
    if (page === "record") await renderRecord();
    if (page === "entity") await renderEntity();
    if (page === "evidence") await renderEvidence();
  }
  main().catch(renderError);
})();
