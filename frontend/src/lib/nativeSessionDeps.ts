// Android/iOS: refresh tokens live only in expo-secure-store (Android Keystore /
// iOS Keychain). Never AsyncStorage, never plain files.
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

import { parseStoredSession, type SessionStorage, type StoredSession } from "./sessionCore";

const KEY = "leia.customer.session.v1";
const OPTIONS: SecureStore.SecureStoreOptions = {
  // iOS: unavailable while locked and never migrated to another device.
  // Android: values are always encrypted with a Keystore key.
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

const storage: SessionStorage = {
  async read() {
    const raw = await SecureStore.getItemAsync(KEY, OPTIONS);
    const session = parseStoredSession(raw);
    if (raw && !session) {
      // Unparseable data can never be used; remove it.
      await SecureStore.deleteItemAsync(KEY, OPTIONS);
    }
    return session;
  },
  async write(session: StoredSession) {
    await SecureStore.setItemAsync(KEY, JSON.stringify(session), OPTIONS);
  },
  async clear() {
    await SecureStore.deleteItemAsync(KEY, OPTIONS);
  },
};

export const nativeSessionDeps: {
  storage: SessionStorage;
  randomId: () => string;
} | null = {
  storage,
  // CSPRNG-backed (SecureRandom on Android).
  randomId: () => Crypto.randomUUID(),
};
