export type RoomTour = { id: string; room: string; tour: string };
export type TourOption = { id: string; label: string; url: string };

export const WHOLE_HOME_ID = "whole-home";
export const WHOLE_HOME_LABEL = "Whole Home";

export function chooseRoomTour(rooms: RoomTour[], selectedId: string | null): RoomTour | undefined {
  return rooms.find((room) => room.id === selectedId) ?? rooms[0];
}

export function roomTourUrl(apiUrl: string, tour: RoomTour | undefined, legacyTour?: string | null): string | undefined {
  if (!tour) return legacyTour || undefined;
  if (!tour.tour.startsWith("/property-media/") || !tour.tour.endsWith("/tour")) return undefined;
  return apiUrl.replace(/\/$/, "") + tour.tour;
}

// Every 360° tour of a listing: the single (whole-home) panorama first, when there is
// one, then each named room in the order the API returns them. Rooms with an unsafe
// tour path are left out.
export function tourOptions(apiUrl: string, rooms: RoomTour[], legacyTour?: string | null): TourOption[] {
  const options: TourOption[] = legacyTour ? [{ id: WHOLE_HOME_ID, label: WHOLE_HOME_LABEL, url: legacyTour }] : [];
  for (const room of rooms) {
    const url = roomTourUrl(apiUrl, room);
    if (url) options.push({ id: room.id, label: room.room, url });
  }
  return options;
}

export function chooseTourOption(options: TourOption[], selectedId: string | null): TourOption | undefined {
  return options.find((option) => option.id === selectedId) ?? options[0];
}
