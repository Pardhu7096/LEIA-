import assert from "node:assert/strict";
import test from "node:test";
import {
  buildPayload,
  fields,
  initialValues,
} from "../src/lib/propertyFields.ts";

const draft = () => ({ ...initialValues(), title: "Madhura Apartments" });

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
    else values[field.key] = "Collected value";
  }
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
