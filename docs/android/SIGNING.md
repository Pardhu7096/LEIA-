# Android signing: current state, release keystore, and key changes

## Current state (verified 2026-11-25)
Both installed demo APKs are signed with the **public React Native template debug key**. This key ships in every React Native template, so anyone can sign an "update" with it.
- SHA-256 `fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c`
- SHA-1 `5e8f16062ea3cd2c4a0d547876baa6f38cabf625`

**Owner decision (2026-11-25):** keep using it for **staging demo builds only**. It must never sign a public or production release. A Google Maps Android key restricted to this SHA-1 is effectively unrestricted.

**No release key exists yet. Creating one needs explicit owner approval.**

## Owner decision (2026-11-28): separate release keys for the pilot

The pilot APKs (versionCode 3) are signed with **two private upload keys, one per app**, created offline by the
owner. A leak or loss of one key never affects the other app.

| App | Keystore file (outside Git) | Alias | Fingerprint recorded in |
|---|---|---|---|
| Customer (`com.leiarealestate.customer`) | `D:\LEIA-signing\leia-customer-upload.p12` | `leia-customer-upload` | `toolchain.json` → `apps.customer.release_cert_sha256` |
| Agent CRM (`com.leiarealestate.agent`) | `D:\LEIA-signing\leia-agent-upload.p12` | `leia-agent-upload` | `toolchain.json` → `apps.agent.release_cert_sha256` |

Rules:
- Each keystore has its **own** password (password manager, at least 20 random characters). PKCS12 uses the same
  password for the store and the key.
- Passwords are typed only at `keytool`'s or the build script's hidden prompt. Never on a command line
  (PowerShell saves typed commands in its history file), never in files, Git, Render, CI, chat or email.
- `D:\LEIA-signing` must not be inside the repository or a cloud-synced folder (OneDrive, Google Drive).
- Only the **public certificate** (`*.cert.pem`) and its fingerprint may leave the machine.
- CI (`check_android_config.py`) refuses a shared fingerprint, the public debug fingerprint, or any tracked
  keystore file.

### Creating the two keystores (owner, offline)

Disconnect Wi-Fi first (optional but recommended), then in a new PowerShell window:

```powershell
$kt = 'D:\Java\temurin-17.0.21+9\bin\keytool.exe'
New-Item -ItemType Directory -Force D:\LEIA-signing | Out-Null
& $kt -genkeypair -v -storetype PKCS12 -keystore D:\LEIA-signing\leia-customer-upload.p12 `
    -alias leia-customer-upload -keyalg RSA -keysize 4096 -validity 10000 `
    -dname "CN=LEIA Customer App, O=LEIA Real Estate, L=Visakhapatnam, C=IN"
& $kt -genkeypair -v -storetype PKCS12 -keystore D:\LEIA-signing\leia-agent-upload.p12 `
    -alias leia-agent-upload -keyalg RSA -keysize 4096 -validity 10000 `
    -dname "CN=LEIA Agent CRM, O=LEIA Real Estate, L=Visakhapatnam, C=IN"
```
`keytool` asks for each password twice (nothing is shown while typing). Then export the **public** certificates
(asks for the password once more):

```powershell
& $kt -exportcert -rfc -keystore D:\LEIA-signing\leia-customer-upload.p12 -alias leia-customer-upload -file D:\LEIA-signing\leia-customer-upload.cert.pem
& $kt -exportcert -rfc -keystore D:\LEIA-signing\leia-agent-upload.p12 -alias leia-agent-upload -file D:\LEIA-signing\leia-agent-upload.cert.pem
```
The fingerprints are read from the `.cert.pem` files (`keytool -printcert -file …`, no password needed) and
recorded in `toolchain.json` in a reviewed commit.

### Backups (do all three, for both keystores)
Windows 11 Home cannot create BitLocker-encrypted USB drives, so each copy is wrapped in an encrypted archive:
1. Install 7-Zip from the official site (7-zip.org).
2. Make one archive per keystore: right-click the `.p12` → 7-Zip → Add to archive → format **7z**, encryption
   **AES-256**, tick **Encrypt file names**, and use a long passphrase that differs from the keystore password
   (store it in the password manager entry).
3. Copy the archives to: (a) a USB drive kept at home, (b) a second USB drive kept at another place, (c) private
   cloud storage. After each copy, compare SHA-256 (`Get-FileHash`) with the original archive.
4. The password manager entries "LEIA Customer upload key" and "LEIA Agent upload key" hold: alias, keystore
   password, archive passphrase, both fingerprints, creation date and the backup locations.
5. Test a restore once: extract one archive to a temporary folder, run `keytool -list -keystore <file>`, then
   delete the temporary copy.

### Building with the release keys
```powershell
.\scripts\android\build-customer.ps1 -Commit <sha> -ApiUrl https://leia-backend-staging.onrender.com -Signing release -KeystorePath D:\LEIA-signing\leia-customer-upload.p12
.\scripts\android\build-agent.ps1    -Commit <sha> -ApiUrl https://leia-backend-staging.onrender.com -Signing release -KeystorePath D:\LEIA-signing\leia-agent-upload.p12
```
The script asks for the password with a hidden prompt. It checks that the key opens and matches the recorded
fingerprint before building. It keeps the password in memory and removes it from the environment, so npm
install scripts, Expo and Gradle never see it. The password is used only for the final `apksigner` step.
Adding `-DryRun` runs only the toolchain, source and key checks.

## Creating a single release (upload) keystore (superseded 2026-11-28 by the per-app keys above)
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
