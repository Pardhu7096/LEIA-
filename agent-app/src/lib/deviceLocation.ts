// Thin expo-location adapter for locationCore (Android, iOS and web).
// Foreground only: no watchPositionAsync, no background tasks, no geocoding services,
// and no last-known/cached position lookups (GEO-M1.1).
import * as Location from "expo-location";

import type { LocationDeps } from "./locationCore";

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
    return {
      latitude: position.coords.latitude,
      longitude: position.coords.longitude,
      accuracy: position.coords.accuracy ?? null,
      timestamp: position.timestamp,
      mocked: Boolean(position.mocked),
    };
  },
  now: () => Date.now(),
};
