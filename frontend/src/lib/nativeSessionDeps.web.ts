// Web builds keep the existing HttpOnly cookie session (/auth/*).
// No refresh token is ever exposed to or stored by browser JavaScript.
import type { SessionStorage } from "./sessionCore";

export const nativeSessionDeps: {
  storage: SessionStorage;
  randomId: () => string;
} | null = null;
