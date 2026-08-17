/* Pure, deterministic business-intelligence selectors and aggregates.
 * The browser consumes the existing public export; this module does not fetch
 * data, infer identities, fuzzy-match names, or mutate the export. */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.EvidenceBI = api;
}(typeof globalThis === "object" ? globalThis : this, () => {
  "use strict";

  const RECORD_FIELD_EVENTS = new Set([
    "value_changed", "date_changed", "supplier_changed", "status_changed"
  ]);

  function isBusinessIntelligenceTarget(source) {
    if (!source) return false;
    if (typeof source.business_intelligence_target === "boolean") {
      return source.business_intelligence_target;
    }
    // `kind` is the existing exporter-owned target classification. Keeping
    // this fallback makes the boundary explicit without changing the export.
    return source.kind === "source";
  }

  function sourceMap(sources) {
    return new Map((sources || []).map(source => [source.id, source]));
  }

  function targetIsBI(targetId, sourcesById) {
    return isBusinessIntelligenceTarget(sourcesById.get(targetId));
  }

  function distinctById(items) {
    const byId = new Map();
    for (const item of items || []) {
      if (item && item.id) byId.set(item.id, item);
    }
    return [...byId.values()];
  }

  function businessRecords(records, sources) {
    const bySource = sourceMap(sources);
    return distinctById((records || []).filter(record =>
      record && targetIsBI(record.source?.target_id, bySource)));
  }

  function businessEvents(changes, sources) {
    const bySource = sourceMap(sources);
    return (changes || []).filter(change =>
      change && targetIsBI(change.target_id, bySource));
  }

  function recordLevelEvents(changes, sources, {includeDisappeared = true} = {}) {
    return businessEvents(changes, sources).filter(change =>
      change.record_id && (includeDisappeared || change.event_type !== "disappeared"));
  }

  function procurementActivityEvents(changes, sources) {
    // A disappearance is an archive projection fact, not procurement action.
    return recordLevelEvents(changes, sources, {includeDisappeared: false});
  }

  function eventWithinWindow(event, generatedAt, days) {
    const eventMs = Date.parse(event?.first_observed_at || "");
    const endMs = Date.parse(generatedAt || "");
    if (!Number.isFinite(eventMs) || !Number.isFinite(endMs) || eventMs > endMs) return false;
    if (!Number.isFinite(days)) return true;
    return eventMs >= endMs - (days * 86400000);
  }

  function windowEvents(events, generatedAt, days) {
    return (events || []).filter(event => eventWithinWindow(event, generatedAt, days));
  }

  function partyEntries(record, role) {
    if (role === "buyer") return record?.buyer ? [record.buyer] : [];
    const suppliers = Array.isArray(record?.suppliers) ? record.suppliers : [];
    if (suppliers.length) return suppliers;
    return record?.supplier ? [record.supplier] : [];
  }

  function entityIds(records, role) {
    const ids = new Set();
    for (const record of distinctById(records)) {
      for (const party of partyEntries(record, role)) {
        if (party?.id) ids.add(party.id);
      }
    }
    return ids;
  }

  function distinctEntityCount(records, role) {
    return entityIds(records, role).size;
  }

  function entitiesForRecords(entities, records, role) {
    const ids = entityIds(records, role);
    const recordIds = new Set(distinctById(records).map(record => record.id));
    return (entities || []).filter(entity => entity.role === role && (
      ids.has(entity.id) || (entity.record_ids || []).some(recordId => recordIds.has(recordId))
    ));
  }

  function recordValue(record) {
    const value = record?.value;
    if (!value || typeof value.amount !== "number" || !Number.isFinite(value.amount)) return null;
    if (typeof value.currency !== "string" || !value.currency.trim()) return null;
    return {amount: value.amount, currency: value.currency.trim()};
  }

  function aggregateValues(records) {
    const unique = distinctById(records);
    const totals = new Map();
    const valuedRecordIds = new Set();
    for (const record of unique) {
      const value = recordValue(record);
      if (!value) continue;
      valuedRecordIds.add(record.id);
      totals.set(value.currency, (totals.get(value.currency) || 0) + value.amount);
    }
    return {
      record_count: unique.length,
      valued_record_count: valuedRecordIds.size,
      missing_value_count: unique.length - valuedRecordIds.size,
      currencies: [...totals.entries()].sort(([a], [b]) => a.localeCompare(b))
        .map(([currency, total]) => ({currency, total}))
    };
  }

  function officialDate(record) {
    return record?.dates?.award || record?.dates?.published || null;
  }

  function latestEventAt(events, recordIds) {
    const ids = recordIds instanceof Set ? recordIds : new Set(recordIds || []);
    return (events || []).filter(event => ids.has(event.record_id))
      .map(event => event.first_observed_at)
      .filter(Boolean)
      .sort((a, b) => Date.parse(b) - Date.parse(a))[0] || null;
  }

  function recentRecords(records, events, generatedAt, days) {
    const ids = new Set(windowEvents(events, generatedAt, days)
      .map(event => event.record_id).filter(Boolean));
    return distinctById(records).filter(record => ids.has(record.id));
  }

  function recordsForEntity(records, entityId, role, entities = []) {
    const exported = (entities || []).find(entity => entity.id === entityId);
    const exportedRecordIds = new Set(exported?.record_ids || []);
    return distinctById(records).filter(record =>
      exportedRecordIds.has(record.id) || partyEntries(record, role).some(party => party.id === entityId));
  }

  function rankedEntities(records, entities, role, events) {
    const uniqueRecords = distinctById(records);
    const roleEntities = entitiesForRecords(entities, uniqueRecords, role);
    return roleEntities.map(entity => {
      const linked = recordsForEntity(uniqueRecords, entity.id, role, entities);
      const counterparties = new Set();
      for (const record of linked) {
        const otherRole = role === "buyer" ? "supplier" : "buyer";
        for (const party of partyEntries(record, otherRole)) if (party.id) counterparties.add(party.id);
      }
      return {
        entity,
        records: linked,
        procurement_count: linked.length,
        counterparties_count: counterparties.size,
        observed_value: aggregateValues(linked),
        latest_activity: latestEventAt(events, new Set(linked.map(record => record.id)))
      };
    }).sort((a, b) => b.procurement_count - a.procurement_count ||
      (Date.parse(b.latest_activity || "") || 0) - (Date.parse(a.latest_activity || "") || 0) ||
      (a.entity.name || a.entity.id).localeCompare(b.entity.name || b.entity.id));
  }

  function categoryCoverage(records) {
    const unique = distinctById(records);
    const withCPV = unique.filter(record => record.classification?.cpv?.length).length;
    return {record_count: unique.length, with_cpv: withCPV,
      coverage: unique.length ? withCPV / unique.length : 0};
  }

  // CSV is a presentation boundary. Keep the JSON value and all browser
  // display values unchanged; only the cell written to a spreadsheet gets
  // this protection. Unicode White_Space plus C0/C1 controls are skipped when
  // finding the first significant character, so a formula cannot hide behind
  // a space, tab, line break, or other leading control character.
  const CSV_LEADING_SPACE_OR_CONTROL = /^[\p{White_Space}\p{Cc}]*/u;
  const CSV_FORMULA_START = /^[=+\-@]/;

  function sanitizeCSVCell(value) {
    const text = value == null ? "" : String(value);
    const significant = text.replace(CSV_LEADING_SPACE_OR_CONTROL, "");
    return CSV_FORMULA_START.test(significant) ? `'${text}` : text;
  }

  function csvEscape(value) {
    const text = sanitizeCSVCell(value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function toCSV(rows, columns) {
    const data = Array.isArray(rows) ? rows : [];
    let descriptors = Array.isArray(columns) ? columns : null;
    if (!descriptors) {
      const keys = [];
      for (const row of data) {
        if (!row || typeof row !== "object" || Array.isArray(row)) continue;
        for (const key of Object.keys(row)) if (!keys.includes(key)) keys.push(key);
      }
      descriptors = keys;
    }
    descriptors = descriptors.map(column => {
      if (typeof column === "string") return {key: column, label: column};
      if (typeof column === "function") return {get: column, label: ""};
      return {key: column?.key, get: column?.get, label: column?.label ?? column?.key ?? ""};
    });
    const values = data.map(row => descriptors.map(column => {
      if (column.get) return column.get(row);
      if (Array.isArray(row)) return row[column.key];
      return row?.[column.key];
    }));
    const lines = [descriptors.map(column => csvEscape(column.label)).join(",")];
    for (const row of values) lines.push(row.map(csvEscape).join(","));
    return `\ufeff${lines.join("\r\n")}\r\n`;
  }

  function csvSafeCell(value) {
    return sanitizeCSVCell(value);
  }

  // Array-row compatibility API used by the browser's complete filtered-view
  // exports. It deliberately quotes every cell while preserving BOM and CRLF.
  function csvEncode(rows) {
    const quote = value => `"${sanitizeCSVCell(value).replace(/"/g, '""')}"`;
    return "\ufeff" + (rows || []).map(row => row.map(quote).join(",")).join("\r\n") + "\r\n";
  }

  function timestampStatusText(status) {
    if (status === "bitcoin-backed") return "Bitcoin-backed";
    if (status === "pending") return "Pending — not Bitcoin-backed";
    if (status === "verification-failed") return "Verification failed";
    return "Awaiting coverage";
  }

  function searchableText(values) {
    return (values || []).flat(Infinity).filter(value => value != null)
      .map(value => String(value).toLocaleLowerCase()).join(" ");
  }

  function tokenMatch(values, query) {
    const haystack = searchableText(values);
    const tokens = String(query || "").trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    return tokens.every(token => haystack.includes(token));
  }

  function universalSearch(records, entities, query, limit = 8) {
    const q = String(query || "").trim();
    if (!q) return {contracts: [], buyers: [], suppliers: []};
    const contracts = distinctById(records).filter(record => tokenMatch([
      record.title, record.id, record.source?.source_record_id, record.buyer?.name,
      record.buyer?.id, partyEntries(record, "supplier").flatMap(p => [p.name, p.id])
    ], q)).slice(0, limit);
    const matching = (entities || []).filter(entity => tokenMatch([entity.name, entity.id], q));
    return {
      contracts,
      buyers: matching.filter(entity => entity.role === "buyer").slice(0, limit),
      suppliers: matching.filter(entity => entity.role === "supplier").slice(0, limit)
    };
  }

  function compareValues(left, right, type = "text", direction = "asc") {
    const missingLeft = left == null || left === "" || (type === "date" && !Number.isFinite(Date.parse(left)));
    const missingRight = right == null || right === "" || (type === "date" && !Number.isFinite(Date.parse(right)));
    // Unknown values remain last in either direction; this is easier to audit.
    if (missingLeft || missingRight) return missingLeft === missingRight ? 0 : missingLeft ? 1 : -1;
    let result;
    if (type === "number") result = Number(left) - Number(right);
    else if (type === "date") result = Date.parse(left) - Date.parse(right);
    else result = String(left).localeCompare(String(right), "en", {numeric: true, sensitivity: "base"});
    return direction === "desc" ? -result : result;
  }

  function stableSort(items, accessor, type = "text", direction = "asc") {
    return (items || []).map((item, index) => ({item, index})).sort((a, b) =>
      compareValues(accessor(a.item), accessor(b.item), type, direction) || a.index - b.index
    ).map(entry => entry.item);
  }

  function paginate(items, page = 1, pageSize = 25) {
    const size = [25, 50, 100].includes(Number(pageSize)) ? Number(pageSize) : 25;
    const pages = Math.max(1, Math.ceil((items || []).length / size));
    const current = Math.min(Math.max(1, Number(page) || 1), pages);
    return {items: (items || []).slice((current - 1) * size, current * size), page: current,
      page_size: size, page_count: pages, total: (items || []).length};
  }

  return {
    RECORD_FIELD_EVENTS,
    isBusinessIntelligenceTarget,
    sourceMap,
    distinctById,
    businessRecords,
    businessEvents,
    recordLevelEvents,
    procurementActivityEvents,
    eventWithinWindow,
    windowEvents,
    partyEntries,
    entityIds,
    distinctEntityCount,
    entitiesForRecords,
    recordValue,
    aggregateValues,
    officialDate,
    latestEventAt,
    recentRecords,
    recordsForEntity,
    rankedEntities,
    categoryCoverage,
    timestampStatusText,
    searchableText,
    tokenMatch,
    universalSearch,
    compareValues,
    stableSort,
    paginate,
    sanitizeCSVCell,
    csvSafeCell,
    csvEscape,
    toCSV,
    recordsToCSV: toCSV,
    csvEncode
  };
}));
