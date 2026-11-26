// Thin expo-location adapter for locationCore (Android, iOS and web).
// Foreground only: no watchPositionAsync, no background tasks, no geocoding services.
import * as Location from "expo-location";

import { LAST_KNOWN_MAX_AGE_MS, type LocationDeps, type RawFix } from "./locationCore";

function toRaw(position: Location.LocationObject): RawFix {
  return {
    latitude: position.coords.latitude,
    longitude: position.coords.longitude,
    accuracy: position.coords.accuracy ?? null,
    timestamp: position.timestamp,
    mocked: Boolean(position.mocked),
  };
}

export const deviceLocationDeps: LocationDeps = {
  servicesEnabled: () => Location.hasServicesEnabledAsync(),
  async requestPermission() {
    // Called only from the "Use my current location" button.
    const response = await Location.requestForegroundPermissionsAsync();
    return { granted: response.status === "granted", canAskAgain: response.canAskAgain };
  },
  async getCurrent() {
    const position = await Location.getCurrentPositionAsync({
      accuracy: Location.Accuracy.High,
      // Android: lets the system offer to switch on high-accuracy location.
      mayShowUserSettingsDialog: true,
    });
    return toRaw(position);
  },
  async getLastKnown(maxAgeMs = LAST_KNOWN_MAX_AGE_MS) {
    const position = await Location.getLastKnownPositionAsync({ maxAge: maxAgeMs });
    return position ? toRaw(position) : null;
  },
  now: () => Date.now(),
};
