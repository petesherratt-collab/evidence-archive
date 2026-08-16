const test = require("node:test");
const assert = require("node:assert/strict");
const BI = require("../bi.js");

const generated = "2026-08-14T06:32:38Z";
const sources = [
  {id: "gov", kind: "source"},
  {id: "control", kind: "control"}
];
const records = [
  {id: "r-old", source: {target_id: "gov"}, value: {amount: 10, currency: "GBP"}, buyer: {id: "b1", name: "Buyer One"}, suppliers: [{id: "s1", name: "Supplier One"}], dates: {award: "2020-01-01T00:00:00Z"}},
  {id: "r-old", source: {target_id: "gov"}, value: {amount: 20, currency: "GBP"}, buyer: {id: "b1", name: "Buyer One"}, suppliers: [{id: "s1", name: "Supplier One"}], dates: {award: "2020-01-01T00:00:00Z"}},
  {id: "r-eur", source: {target_id: "gov"}, value: {amount: 2, currency: "EUR"}, buyer: {id: "b2", name: "Buyer Two"}, suppliers: [{id: "s2", name: "Supplier Two"}], dates: {published: "2020-02-01T00:00:00Z"}},
  {id: "r-null", source: {target_id: "gov"}, value: null, buyer: null, suppliers: []},
  {id: "r-control", source: {target_id: "control"}, value: {amount: 999, currency: "GBP"}, buyer: {id: "bc", name: "Control Buyer"}, suppliers: [{id: "sc", name: "Control Supplier"}]}
];
const changes = [
  {id: "gov-first", target_id: "gov", record_id: "r-old", event_type: "first_seen", first_observed_at: "2026-08-14T05:32:38Z"},
  {id: "gov-value", target_id: "gov", record_id: "r-old", event_type: "value_changed", first_observed_at: "2026-08-13T05:32:38Z"},
  {id: "gov-disappeared", target_id: "gov", record_id: "r-eur", event_type: "disappeared", first_observed_at: "2026-08-14T04:32:38Z"},
  {id: "gov-source", target_id: "gov", record_id: null, event_type: "raw_response_changed", first_observed_at: "2026-08-14T03:32:38Z"},
  {id: "control-first", target_id: "control", record_id: "r-control", event_type: "first_seen", first_observed_at: "2026-08-14T05:32:38Z"},
  {id: "future", target_id: "gov", record_id: "r-eur", event_type: "first_seen", first_observed_at: "2026-08-15T05:32:38Z"}
];

test("control target is excluded from every BI aggregate", () => {
  const businessRecords = BI.businessRecords(records, sources);
  const businessEvents = BI.businessEvents(changes, sources);
  assert.deepEqual(businessRecords.map(record => record.id), ["r-old", "r-eur", "r-null"]);
  assert.equal(businessEvents.some(event => event.target_id === "control"), false);
  assert.equal(BI.distinctEntityCount(businessRecords, "buyer"), 2);
  assert.equal(BI.distinctEntityCount(businessRecords, "supplier"), 2);
  assert.equal(BI.aggregateValues(businessRecords).currencies.some(item => item.total === 999), false);
});

test("record activity windows use government record events only", () => {
  const activity = BI.procurementActivityEvents(changes, sources);
  assert.deepEqual(activity.map(event => event.id), ["gov-first", "gov-value", "future"]);
  assert.equal(BI.windowEvents(activity, generated, 1).length, 1);
  assert.equal(BI.windowEvents(activity, generated, 7).length, 2);
  assert.equal(BI.windowEvents(activity, generated, 30).length, 2);
  assert.equal(BI.windowEvents(activity, generated, 30).some(event => event.id === "future"), false);
});

test("stable record IDs deduplicate historical/current appearances", () => {
  const unique = BI.businessRecords(records, sources);
  assert.equal(unique.length, 3);
  assert.equal(unique.find(record => record.id === "r-old").value.amount, 20);
  assert.equal(BI.aggregateValues(unique).record_count, 3);
});

test("values exclude nulls and remain separated by currency", () => {
  const aggregate = BI.aggregateValues(BI.businessRecords(records, sources));
  assert.equal(aggregate.valued_record_count, 2);
  assert.equal(aggregate.missing_value_count, 1);
  assert.deepEqual(aggregate.currencies, [
    {currency: "EUR", total: 2}, {currency: "GBP", total: 20}
  ]);
  assert.equal(BI.officialDate(records[2]), "2020-02-01T00:00:00Z");
  assert.notEqual(BI.officialDate(records[2]), changes[0].first_observed_at);
});

test("timestamp status never upgrades pending to Bitcoin-backed", () => {
  assert.match(BI.timestampStatusText("bitcoin-backed"), /Bitcoin-backed/);
  assert.match(BI.timestampStatusText("pending"), /Pending/);
  assert.doesNotMatch(BI.timestampStatusText("pending"), /^Bitcoin-backed$/);
});

test("aggregate and classification output is deterministic", () => {
  const first = JSON.stringify({
    records: BI.businessRecords(records, sources),
    events: BI.procurementActivityEvents(changes, sources),
    values: BI.aggregateValues(BI.businessRecords(records, sources))
  });
  const second = JSON.stringify({
    records: BI.businessRecords(records, sources),
    events: BI.procurementActivityEvents(changes, sources),
    values: BI.aggregateValues(BI.businessRecords(records, sources))
  });
  assert.equal(first, second);
});

test("universal search groups deterministic substring and token matches", () => {
  const entities = [{id: "b1", role: "buyer", name: "Buyer One"}, {id: "s1", role: "supplier", name: "Supplier One"}];
  const result = BI.universalSearch(records, entities, "buyer one");
  assert.deepEqual(result.contracts.map(item => item.id), ["r-old"]);
  assert.deepEqual(result.buyers.map(item => item.id), ["b1"]);
  assert.deepEqual(result.suppliers, []);
});

test("stable sorting keeps nulls last and pagination bounds the DOM slice", () => {
  const items = [{id: "a", value: 2}, {id: "b", value: null}, {id: "c", value: 2}, {id: "d", value: 1}];
  assert.deepEqual(BI.stableSort(items, item => item.value, "number", "asc").map(item => item.id), ["d", "a", "c", "b"]);
  assert.deepEqual(BI.stableSort(items, item => item.value, "number", "desc").map(item => item.id), ["a", "c", "d", "b"]);
  const many = Array.from({length: 127}, (_, index) => index);
  assert.deepEqual(BI.paginate(many, 2, 50), {items: many.slice(50, 100), page: 2, page_size: 50, page_count: 3, total: 127});
});

test("CSV is UTF-8, quoted, newline-safe, and neutralises spreadsheet formulas", () => {
  const csv = BI.csvEncode([["name", "note"], ["=2+2", "comma, quote \" and\nnewline"], ["+cmd", "-1"], ["@x", "safe"]]);
  assert.equal(csv.charCodeAt(0), 0xfeff);
  assert.match(csv, /"'=2\+2"/);
  assert.match(csv, /"'\+cmd"/);
  assert.match(csv, /"'-1"/);
  assert.match(csv, /"'@x"/);
  assert.match(csv, /"comma, quote "" and\nnewline"/);
});
