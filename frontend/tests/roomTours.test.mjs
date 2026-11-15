import test from "node:test";
import assert from "node:assert/strict";
import { chooseRoomTour, chooseTourOption, roomTourUrl, tourOptions, WHOLE_HOME_ID } from "../src/lib/roomTours.ts";

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

// F1: the single (whole-home) panorama stays available next to every room tour.

const legacy = "https://example.test/property-media/single-id/tour";

test("single panorama and rooms: Whole Home first, then every room", () => {
  const options = tourOptions("https://example.test", rooms, legacy);
  assert.deepEqual(options.map((o) => o.label), ["Whole Home", "Hall", "Bedroom"]);
  assert.equal(options[0].url, legacy);
  assert.equal(options[2].url, "https://example.test/property-media/bed-id/tour");
});
test("Whole Home is the default view and each room stays selectable", () => {
  const options = tourOptions("https://example.test", rooms, legacy);
  assert.equal(chooseTourOption(options, null)?.id, WHOLE_HOME_ID);
  assert.equal(chooseTourOption(options, "bed-id")?.label, "Bedroom");
  assert.equal(chooseTourOption(options, WHOLE_HOME_ID)?.url, legacy);
});
test("only a single panorama: one option, no room buttons needed", () => {
  const options = tourOptions("https://example.test", [], legacy);
  assert.equal(options.length, 1);
  assert.equal(options[0].label, "Whole Home");
});
test("only rooms: no Whole Home entry", () => {
  const options = tourOptions("https://example.test", rooms, null);
  assert.deepEqual(options.map((o) => o.id), ["hall-id", "bed-id"]);
});
test("no tours at all: nothing to show", () => {
  assert.deepEqual(tourOptions("https://example.test", [], null), []);
  assert.equal(chooseTourOption([], null), undefined);
});
test("a room with an unsafe tour path is left out, others stay", () => {
  const unsafe = { id: "x", room: "Evil", tour: "https://evil.example/tour" };
  const options = tourOptions("https://example.test", [unsafe, ...rooms], legacy);
  assert.deepEqual(options.map((o) => o.id), [WHOLE_HOME_ID, "hall-id", "bed-id"]);
});
test("a removed room selection falls back to Whole Home", () => {
  const options = tourOptions("https://example.test", rooms.slice(0, 1), legacy);
  assert.equal(chooseTourOption(options, "bed-id")?.id, WHOLE_HOME_ID);
});
