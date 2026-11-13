export type RoomTour = { id: string; room: string; tour: string };

export function chooseRoomTour(rooms: RoomTour[], selectedId: string | null): RoomTour | undefined {
  return rooms.find((room) => room.id === selectedId) ?? rooms[0];
}

export function roomTourUrl(apiUrl: string, tour: RoomTour | undefined, legacyTour?: string | null): string | undefined {
  if (!tour) return legacyTour || undefined;
  if (!tour.tour.startsWith("/property-media/") || !tour.tour.endsWith("/tour")) return undefined;
  return apiUrl.replace(/\/$/, "") + tour.tour;
}
