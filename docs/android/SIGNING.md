# Android signing: current state, release keystore, and key changes

## Current state (verified 2026-11-25)
Both installed demo APKs are signed with the **public React Native template debug key**. This key ships in every React Native template, so anyone can sign an "update" with it.
- SHA-256 `fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c`
- SHA-1 `5e8f16062ea3cd2c4a0d547876baa6f38cabf625`

**Owner decision (2026-11-25):** keep using it for **staging demo builds only**. It must never sign a public or production release. A Google Maps Android key restricted to this SHA-1 is effectively unrestricted.

**No release key exists yet. Creating one needs explicit owner approval.**

## Creating the release (upload) keystore, when approved
Run on the owner's machine, offline. The keystore is never created inside the repository.
```powershell
$jdk = 'D:\Java\temurin-17.0.21+9'
New-Item -ItemType Directory -Force E:\LEIA-signing | Out-Null          # E: = encrypted USB drive (BitLocker To Go)
& "$jdk\bin\keytool.exe" -genkeypair -v -keystore E:\LEIA-signing\leia-upload.jks `
    -alias leia-upload -keyalg RSA -keysize 4096 -validity 10000 `
    -dname "CN=LEIA Real Estate, O=LEIA, C=IN"
# keytool prompts for the passwords. Never pass them on the command line.
& "$jdk\bin\keytool.exe" -list -v -keystore E:\LEIA-signing\leia-upload.jks -alias leia-upload
```
Then:
1. **Record only the public fingerprint**: put the SHA-256 in `scripts/android/toolchain.json` → `signing.release_cert_sha256` (PR reviewed). The SHA-1 goes into the Google Maps Android key restriction.
2. **Passwords** go into the password manager entry "LEIA Android upload key": alias, both passwords, fingerprints, creation date.
3. **Backups** (do all three):
   - Copy 1: an encrypted USB drive kept at home.
   - Copy 2: a second encrypted USB drive kept at a different location.
   - Copy 3: an encrypted archive (7-Zip AES-256 with a long passphrase) in private cloud storage, with the passphrase only in the password manager.
   - After each copy, check its SHA-256 against the original.
4. **Never** put the keystore or its passwords in Git, Render, CI secrets, chat or email. The build reads the password from `LEIA_KEYSTORE_PASSWORD`, set in the current window only, and signs with `apksigner --ks-pass env:...`.
5. Use **Google Play App Signing** if the app goes to Play. Google then holds the app-signing key, and this keystore becomes an *upload* key that can be reset through Play Console support if lost.

## Recovery
- **Lost keystore, Play App Signing in use:** request an upload-key reset in Play Console. Users are unaffected.
- **Lost keystore, no Play App Signing (sideloaded APKs):** existing installs can **never** be updated again. Every user must uninstall and install a build signed with a new key. Hence the three backups.
- **Leaked keystore or password:** stop distributing, create a new key, rotate with APK Signature Scheme v3 (`apksigner rotate` + `--lineage`) if the old key is still trusted, update the Maps key SHA-1, and treat leaked builds as untrusted.
- **Annual check:** confirm each backup opens and its fingerprint matches `toolchain.json`.

## What changing the signing key does to existing installations
Android accepts an update only if it is signed by the **same certificate** as the installed app (or by a key in its v3 rotation lineage).
- Installing an APK signed with a different key over an existing install fails with `INSTALL_FAILED_UPDATE_INCOMPATIBLE` ("App not installed").
- The user must **uninstall first**, which deletes the app's local data:
  - **Customer App:** the saved sign-in in Android Keystore / `expo-secure-store` is lost, so the user signs in again. Server data (saved chats, account) is unaffected.
  - **Agent CRM:** anything held only on the device (unsent form input) is lost. Drafts already saved to the server are unaffected.
- The same `versionCode` rules apply after the switch: the new build needs a versionCode at least as high as anything that will later be installed over it.
- Google Maps Android keys restricted by package plus SHA-1 must list the new SHA-1, or maps stop loading.
- For the current demo phones (versionCode 1, public debug key), the switch to a release key is a **one-time uninstall and reinstall** per phone. Staging builds can stay on the debug key until then (owner decision).
