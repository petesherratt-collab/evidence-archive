const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const BI = require("../bi.js");

const root = path.resolve(__dirname, "..");
const evidence = path.join(root, "public", "evidence");
const read = relative => JSON.parse(fs.readFileSync(path.join(evidence, relative), "utf8"));

test("stable record, entity, and evidence links resolve in the structured export", () => {
  const sources = read("sources.json").sources;
  const entities = read("entities.json").entities;
  const records = read("records.json").records;
  const changes = read("changes.json").changes;
  const sourceIds = new Set(sources.map(source => source.id));
  const entityIds = new Set(entities.map(entity => entity.id));
  const recordIds = new Set(records.map(record => record.id));
  const observationIds = new Set();
  for (const source of sources) {
    const observations = read(`observations/${source.id}.json`).observations;
    for (const observation of observations) observationIds.add(observation.id);
  }
  assert.equal(recordIds.size, records.length, "record IDs must be distinct");
  assert.equal(entityIds.size, entities.length, "entity IDs must be distinct");
  const businessRecords = BI.businessRecords(records, sources);
  assert.equal(businessRecords.length, 1888, "displayed procurement count must use stable records");
  assert.equal(BI.entitiesForRecords(entities, businessRecords, "buyer").length, 870, "displayed buyer count must use stable entity IDs");
  assert.equal(BI.entitiesForRecords(entities, businessRecords, "supplier").length, 2399, "displayed supplier count must use stable entity IDs");
  for (const record of records) {
    assert.equal(sourceIds.has(record.source.target_id), true, record.id);
    assert.equal(record.buyer == null || entityIds.has(record.buyer.id), true, record.id);
    for (const supplier of record.suppliers || []) assert.equal(entityIds.has(supplier.id), true, record.id);
    for (const observationId of record.evidence.observation_ids) assert.equal(observationIds.has(observationId), true, observationId);
  }
  for (const entity of entities) for (const recordId of entity.record_ids) assert.equal(recordIds.has(recordId), true, recordId);
  for (const change of changes) {
    assert.equal(sourceIds.has(change.target_id), true, change.id);
    assert.equal(observationIds.has(change.observation_id), true, change.observation_id);
    assert.equal(change.record_id == null || recordIds.has(change.record_id), true, change.id);
  }
});

test("snapshot file hashes match the manifest and renderers avoid HTML injection sinks", () => {
  const manifest = read("manifest.json");
  for (const file of manifest.files) {
    const bytes = fs.readFileSync(path.join(evidence, file.path));
    assert.equal(bytes.length, file.bytes, file.path);
    assert.equal(crypto.createHash("sha256").update(bytes).digest("hex"), file.sha256, file.path);
  }
  const app = fs.readFileSync(path.join(root, "app.js"), "utf8");
  const bi = fs.readFileSync(path.join(root, "bi.js"), "utf8");
  assert.equal(app.includes("innerHTML"), false);
  assert.equal(app.includes("outerHTML"), false);
  assert.equal(bi.includes("innerHTML"), false);
});
