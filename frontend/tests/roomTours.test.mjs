import test from "node:test";
import assert from "node:assert/strict";
import { chooseRoomTour, roomTourUrl } from "../src/lib/roomTours.ts";

const rooms = [
  { id: "hall-id", room: "Hall", tour: "/property-media/hall-id/tour" },
  { id: "bed-id", room: "Bedroom", tour: "/property-media/bed-id/tour" },
];

test("initial selection shows first available room", () => {
  assert.equal(chooseRoomTour(rooms, null)?.id, "hall-id");
});
test("selecting another room updates the active tour", () => {
  assert.equal(chooseRoomTour(rooms, "bed-id")?.room, "Bedroom");
});
test("removed room selection falls back to available room", () => {
  assert.equal(chooseRoomTour(rooms.slice(0, 1), "bed-id")?.id, "hall-id");
});
test("empty room list uses legacy tour", () => {
  assert.equal(roomTourUrl("https://example.test", undefined, "https://example.test/legacy"), "https://example.test/legacy");
});
test("missing room and legacy tour displays no tour", () => {
  assert.equal(roomTourUrl("https://example.test", undefined, null), undefined);
});
test("room URL joins the configured API base and tour route", () => {
  assert.equal(roomTourUrl("https://example.test/", rooms[1]), "https://example.test/property-media/bed-id/tour");
});
test("invalid room tour paths are rejected", () => {
  assert.equal(roomTourUrl("https://example.test", { id: "x", room: "x", tour: "https://evil.example/tour" }), undefined);
});
