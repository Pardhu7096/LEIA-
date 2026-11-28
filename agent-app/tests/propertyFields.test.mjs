import assert from "node:assert/strict";
import test from "node:test";
import {
  buildPayload,
  builderSuggestions,
  fields,
  initialValues,
  locationMetaFromServer,
  PROJECT_FACT_KEYS,
} from "../src/lib/propertyFields.ts";

const draft = () => ({ ...initialValues(), title: "Madhura Apartments" });

const gps = { source: "gps", accuracyM: 12.3, capturedAt: "2026-11-26T04:59:59.000Z" };

test("GPS-captured coordinates carry accuracy and capture time", () => {
  const payload = buildPayload({ ...draft(), latitude: "17.723105", longitude: "83.301248" }, false, gps);
  assert.equal(payload.latitude, 17.723105);
  assert.equal(payload.longitude, 83.301248);
  assert.equal(payload.location_source, "gps");
  assert.equal(payload.location_accuracy_m, 12.3);
  assert.equal(payload.location_captured_at, "2026-11-26T04:59:59.000Z");
});

test("manual coordinates are labelled manual with no GPS metadata", () => {
  for (const meta of [null, { source: "manual" }]) {
    const payload = buildPayload({ ...draft(), latitude: "17.7", longitude: "83.3" }, false, meta);
    assert.equal(payload.location_source, "manual");
    assert.equal(payload.location_accuracy_m, null);
    assert.equal(payload.location_captured_at, null);
  }
});

test("clearing coordinates clears location metadata, even after a GPS capture", () => {
  const payload = buildPayload(draft(), false, gps);
  assert.equal(payload.latitude, null);
  assert.equal(payload.longitude, null);
  assert.equal(payload.location_source, null);
  assert.equal(payload.location_accuracy_m, null);
  assert.equal(payload.location_captured_at, null);
});

test("a half-filled coordinate pair is still rejected", () => {
  assert.throws(() => buildPayload({ ...draft(), latitude: "17.7" }, false, gps), /both coordinates/);
});

test("location metadata is read back from the server, tolerating older servers", () => {
  assert.deepEqual(
    locationMetaFromServer({ location_source: "gps", location_accuracy_m: 8, location_captured_at: "2026-11-26T05:00:00Z" }),
    { source: "gps", accuracyM: 8, capturedAt: "2026-11-26T05:00:00Z" },
  );
  assert.deepEqual(locationMetaFromServer({ location_source: "manual" }), { source: "manual" });
  assert.equal(locationMetaFromServer({}), null);
  assert.equal(locationMetaFromServer({ location_source: "gps" }), null);
});

test("unknown values and zero counts remain distinct", () => {
  const payload = buildPayload({ ...draft(), car_parking_spaces: "0" }, false);
  assert.equal(payload.car_parking_spaces, 0);
  assert.equal(payload.bike_parking_spaces, null);
  assert.equal(payload.water_municipal, "unknown");
  assert.equal(payload.electricity_connection, "unknown");
});

test("blank optional values clear saved data and consent can be withdrawn", () => {
  const payload = buildPayload(
    { ...draft(), owner_phone: "", water_notes: "" },
    false,
  );
  assert.equal(payload.owner_phone, null);
  assert.equal(payload.water_notes, null);
  assert.equal(payload.listing_consent, false);
});

test("all editable fields survive reopening and serializing a draft", () => {
  const values = draft();
  for (const field of fields) {
    if (field.choices) values[field.key] = field.choices.at(-1)[0];
    else if (field.kind === "date") values[field.key] = "2026-11-04";
    else if (field.kind === "coordinate") values[field.key] = "17.123456";
    else if (field.kind === "decimal") values[field.key] = "1200.25";
    else if (field.kind === "integer") values[field.key] = "1";
    else if (field.key === "pincode") values[field.key] = "530001";
    else if (field.key === "owner_phone") values[field.key] = "+91 9876543210";
    else values[field.key] = "Collected value";
  }
  // Migration 014 rules: a real RERA number, and a possession date needs "under construction".
  values.rera_number = "P02400012345";
  values.construction_status = "under_construction";
  const saved = buildPayload(values, true);
  const reopened = Object.fromEntries(
    fields.map((field) => [
      field.key,
      saved[field.key] === null ? "" : String(saved[field.key]),
    ]),
  );
  assert.deepEqual(buildPayload(reopened, true), saved);
  assert.equal(saved.area_sqft, "1200.25");
});

for (const changes of [
  { title: " " },
  { car_parking_spaces: "-1" },
  { bedrooms: "1.5" },
  { price: "65,00,000" },
  { water_storage_litres: "1e3" },
  { latitude: "17" },
  { latitude: "91", longitude: "83" },
  { water_checked_on: "2026-02-30" },
  { pincode: "123" },
  { power_backup: "guessed" },
  { area_sqft: "12.123" },
]) {
  test(`rejects invalid entry ${JSON.stringify(changes)}`, () => {
    assert.throws(() => buildPayload({ ...draft(), ...changes }, false));
  });
}

for (const changes of [
  { owner_phone: "not a phone" },
  { title: "X".repeat(201) },
  { description: "X".repeat(10001) },
]) {
  test(`rejects malformed or oversized input ${Object.keys(changes)[0]}`, () => {
    assert.throws(() => buildPayload({ ...draft(), ...changes }, false));
  });
}

test("requires owner contact details when listing consent is recorded", () => {
  assert.throws(() => buildPayload(draft(), true));
  const payload = buildPayload({ ...draft(), owner_name: "Owner", owner_phone: "+91 9876543210" }, true);
  assert.equal(payload.listing_consent, true);
});


// Migration 014: builder and project facts.
test("project facts default to not recorded, amenities to an empty list", () => {
  const payload = buildPayload(draft(), false);
  for (const key of ["lift", "gated_community", "security", "construction_status", "rera_source", "project_source"]) {
    assert.equal(payload[key], "unknown", key);
  }
  assert.deepEqual(payload.amenities, []);
  assert.equal(payload.builder_name, null);
  assert.equal(payload.possession_date, null);
});

test("amenities are sent as a de-duplicated, sorted list of known values", () => {
  const payload = buildPayload({ ...draft(), amenities: "pool,gym,pool" }, false);
  assert.deepEqual(payload.amenities, ["gym", "pool"]);
  assert.throws(() => buildPayload({ ...draft(), amenities: "gym,helipad" }, false), /valid values/);
});

test("explicit no is kept distinct from unknown", () => {
  const payload = buildPayload({ ...draft(), lift: "no", gated_community: "yes" }, false);
  assert.equal(payload.lift, "no");
  assert.equal(payload.gated_community, "yes");
});

test("project fact validation gives plain-language messages", () => {
  assert.throws(
    () => buildPayload({ ...draft(), possession_date: "2027-03-31" }, false),
    /under construction to record an expected possession date/,
  );
  assert.equal(
    buildPayload({ ...draft(), construction_status: "under_construction", possession_date: "2027-03-31" }, false)
      .possession_date,
    "2027-03-31",
  );
  assert.throws(() => buildPayload({ ...draft(), floor_number: "9", total_floors: "5" }, false), /above the building/);
  assert.throws(() => buildPayload({ ...draft(), rera_source: "owner_reported" }, false), /RERA registration number/);
  assert.throws(() => buildPayload({ ...draft(), rera_number: "bad number!" }, false), /RERA number exactly/);
  const ok = buildPayload({ ...draft(), rera_number: "P02400012345", rera_source: "owner_reported" }, false);
  assert.equal(ok.rera_number, "P02400012345");
});

test("builder suggestions ignore case, spacing and punctuation and stop at an exact match", () => {
  const known = ["Raju Constructions", "Sri Sai Developers", "Lakshmi Homes"];
  assert.deepEqual(builderSuggestions(known, "raju"), ["Raju Constructions"]);
  assert.deepEqual(builderSuggestions(known, "SRI-SAI"), ["Sri Sai Developers"]);
  assert.deepEqual(builderSuggestions(known, "raju  constructions."), []);
  assert.deepEqual(builderSuggestions(known, ""), []);
});

test("every project fact field is a form field (old servers fall back to defaults)", () => {
  const keys = new Set(fields.map((field) => field.key));
  for (const key of PROJECT_FACT_KEYS) assert.ok(keys.has(key), key);
});
