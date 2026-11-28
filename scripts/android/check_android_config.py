"""CI guard for LEIA Android build configuration (no network, no secrets needed).

Fails when:
  - an app.json loses its pinned application id / display name, or versionCode < minimum
  - background location (or a location foreground service) is requested anywhere in config
  - a keystore, key or service-account file is tracked by Git
  - a Google API key, private key or keystore password literal appears in tracked text files
  - toolchain.json is missing a pin or the build scripts are missing
Run:  python scripts/android/check_android_config.py
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLCHAIN = ROOT / "scripts" / "android" / "toolchain.json"
errors = []


def err(msg):
    errors.append(msg)


tc = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
for key in ("node", "java", "gradle", "android_sdk", "android_app", "apps", "signing", "forbidden_permissions"):
    if key not in tc:
        err(f"toolchain.json missing '{key}'")
if not re.fullmatch(r"[0-9a-f]{64}", tc.get("gradle", {}).get("distribution_sha256", "")):
    err("toolchain.json gradle.distribution_sha256 must be a SHA-256")
if not re.fullmatch(r"[0-9a-f]{64}", tc.get("signing", {}).get("debug_public_cert_sha256", "")):
    err("toolchain.json signing.debug_public_cert_sha256 must be a SHA-256")
if tc.get("signing", {}).get("release_cert_sha256") is not None:
    err("toolchain.json signing.release_cert_sha256 must stay null: release keys are per app (apps.<app>.release_cert_sha256)")
# Separate release (upload) key per app: public fingerprints only, distinct, never the public debug key.
debug_fp = tc.get("signing", {}).get("debug_public_cert_sha256")
release_fps = {}
for name, app in tc.get("apps", {}).items():
    fp = app.get("release_cert_sha256")
    if not re.fullmatch(r"leia-[a-z]+-upload", app.get("release_key_alias") or ""):
        err(f"toolchain.json apps.{name}.release_key_alias must be set (leia-<app>-upload)")
    if fp is None:
        continue
    if not re.fullmatch(r"[0-9a-f]{64}", fp):
        err(f"toolchain.json apps.{name}.release_cert_sha256 must be null or a lowercase SHA-256 (fingerprint only)")
    elif fp == debug_fp:
        err(f"toolchain.json apps.{name}.release_cert_sha256 is the public debug key")
    elif fp in release_fps.values():
        err(f"toolchain.json apps.{name}.release_cert_sha256 is shared with another app; each app needs its own key")
    release_fps[name] = fp

forbidden = set(tc.get("forbidden_permissions", []))
for name, app in tc.get("apps", {}).items():
    path = ROOT / app["dir"] / "app.json"
    expo = json.loads(path.read_text(encoding="utf-8"))["expo"]
    android = expo.get("android", {})
    if android.get("package") != app["application_id"]:
        err(f"{path.relative_to(ROOT)}: android.package must be {app['application_id']}")
    if expo.get("name") != app["display_name"]:
        err(f"{path.relative_to(ROOT)}: name must be '{app['display_name']}'")
    vc = android.get("versionCode")
    if not isinstance(vc, int) or vc < app["min_version_code"]:
        err(f"{path.relative_to(ROOT)}: android.versionCode must be an integer >= {app['min_version_code']}")
    perms = set(android.get("permissions", []))
    blocked = set(android.get("blockedPermissions", []))
    if perms & forbidden:
        err(f"{path.relative_to(ROOT)}: forbidden permissions requested: {sorted(perms & forbidden)}")
    app_forbidden = set(app.get("forbidden_permissions", []))
    if perms & app_forbidden:
        err(f"{path.relative_to(ROOT)}: forbidden permissions requested: {sorted(perms & app_forbidden)}")
    if app_forbidden - blocked:
        err(f"{path.relative_to(ROOT)}: android.blockedPermissions must include {sorted(app_forbidden - blocked)}")
    text = json.dumps(expo)
    if re.search(r'"isAndroidBackgroundLocationEnabled"\s*:\s*true|"isAndroidForegroundServiceEnabled"\s*:\s*true', text):
        err(f"{path.relative_to(ROOT)}: background location / location foreground service must stay disabled")
    if "googleMapsApiKey" in text or re.search(r"AIza[0-9A-Za-z_\-]{35}", text):
        err(f"{path.relative_to(ROOT)}: Maps API keys must come from build secrets, not app.json")
    pkg = json.loads((ROOT / app["dir"] / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / app["dir"] / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    for dep, pin in (("expo", app["expo"]), ("react-native", app["react_native"])):
        got = lock.get(f"node_modules/{dep}", {}).get("version")
        if got != pin:
            err(f"{app['dir']}/package-lock.json: {dep} is {got}, toolchain.json pins {pin}")
    # Foreground-only location (GEO-M1): if expo-location is configured, background
    # location and the location foreground service must be explicitly off AND blocked.
    for plugin in expo.get("plugins", []):
        name, options = (plugin[0], plugin[1] if len(plugin) > 1 else {}) if isinstance(plugin, list) else (plugin, {})
        if name != "expo-location":
            continue
        if options.get("isAndroidBackgroundLocationEnabled") is not False or options.get("isAndroidForegroundServiceEnabled") is not False:
            err(f"{path.relative_to(ROOT)}: expo-location must set isAndroidBackgroundLocationEnabled and isAndroidForegroundServiceEnabled to false")
        for needed in ("android.permission.ACCESS_BACKGROUND_LOCATION", "android.permission.FOREGROUND_SERVICE_LOCATION"):
            if needed not in blocked:
                err(f"{path.relative_to(ROOT)}: android.blockedPermissions must include {needed} when expo-location is used")
    _ = pkg

for script in ("build-android.ps1", "build-agent.ps1", "build-customer.ps1", "inspect-apk.ps1"):
    if not (ROOT / "scripts" / "android" / script).exists():
        err(f"missing scripts/android/{script}")

tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
secret_files = re.compile(r"(\.(jks|keystore|p12|pfx|pem|key)$)|((^|/)keystore\.properties$)|((^|/)google-services\.json$)|((^|/)service-account[^/]*\.json$)|((^|/)\.env$)", re.I)
for f in tracked:
    if secret_files.search(f):
        err(f"secret-bearing file is tracked by Git: {f}")

patterns = {
    "Google API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |)PRIVATE KEY-----"),
    "keystore password literal": re.compile(r"(?i)(store|key)Password\s*[=:]\s*['\"]?[^\s'\"$%{]{4,}"),
}
text_ext = {".json", ".js", ".ts", ".tsx", ".py", ".ps1", ".md", ".yml", ".yaml", ".gradle", ".properties", ".xml", ".txt", ".env"}
for f in tracked:
    p = ROOT / f
    if p.suffix.lower() not in text_ext or not p.is_file() or "package-lock" in f:
        continue
    try:
        content = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for label, rx in patterns.items():
        if rx.search(content):
            err(f"{f}: looks like a {label}")

gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
for needed in ("*.jks", "*.keystore", "keystore.properties", "*.apk", "*.aab"):
    if needed not in gitignore.split():
        err(f".gitignore must contain '{needed}'")

if errors:
    print("Android config check FAILED:")
    for e in errors:
        print("  -", e)
    sys.exit(1)
print(f"Android config check passed ({len(tc['apps'])} apps, {len(tracked)} tracked files scanned).")
