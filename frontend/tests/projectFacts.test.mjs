import assert from "node:assert/strict";
import test from "node:test";

import { projectFacts } from "../src/lib/projectFacts.ts";

const value = (facts, label) => facts.find((fact) => fact.label === label)?.value;

test("older listings without project facts show nothing extra", () => {
  assert.deepEqual(projectFacts({ title: "Old" }), []);
});

test("unknown facts read 'Not provided', never yes", () => {
  const facts = projectFacts({ lift: "unknown", gated_community: "unknown", security: "unknown",
    construction_status: "unknown", amenities: [], rera_verified: false, rera_number: null, project_source: "unknown" });
  for (const label of ["Builder", "Construction", "Floor", "Lift", "Gated community", "Security", "Amenities"]) {
    assert.equal(value(facts, label), "Not provided", label);
  }
  assert.equal(value(facts, "RERA"), "Not verified");
});

test("recorded facts carry their source; explicit no stays no", () => {
  const facts = projectFacts({ builder: "Raju Constructions", project_name: "Raju Greens",
    construction_status: "under_construction", possession_date: "2027-03-31", floor_number: 0, total_floors: 12,
    lift: "no", gated_community: "yes", security: "guard_and_cctv", amenities: ["gym", "pool", "helipad"],
    project_source: "owner_reported", rera_verified: false, rera_number: null });
  assert.equal(value(facts, "Builder"), "Raju Constructions");
  assert.equal(value(facts, "Project"), "Raju Greens");
  assert.equal(value(facts, "Construction"), "Under construction, expected possession 2027-03-31 (estimate) (owner reported)");
  assert.equal(value(facts, "Floor"), "Ground of 12 (owner reported)");
  assert.equal(value(facts, "Lift"), "No (owner reported)");
  assert.equal(value(facts, "Gated community"), "Yes (owner reported)");
  assert.equal(value(facts, "Amenities"), "Gym, Swimming pool (owner reported)");
});

test("a RERA number is shown only when verified", () => {
  assert.equal(value(projectFacts({ lift: "unknown", rera_verified: true, rera_number: "P02400012345" }), "RERA"),
    "P02400012345 (verified by LEIA)");
  // Even if a number were present, an unverified one is never displayed.
  assert.equal(value(projectFacts({ lift: "unknown", rera_verified: false, rera_number: "P02400012345" }), "RERA"),
    "Not verified");
});
