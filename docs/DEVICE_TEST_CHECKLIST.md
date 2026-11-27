# Physical-device test checklist: Customer app and Agent CRM (staging)

Status: **pending manual verification.** Nothing here has been run on a real device or emulator yet.

Use staging builds only, signed in with test accounts. Don't publish listings that real testers would
see without the owner's approval. For each run, record:
- device model
- Android version
- keyboard (Gboard or Samsung)
- screen size
- app build (APK file name and SHA-256 from `D:\LEIA-Releases`)
- date

Mark each item PASS, FAIL or N/A, and attach a screenshot for every FAIL.

## A. Customer app

### A1. Install and sessions
- [ ] Install over the previous build: the existing session survives the update.
- [ ] Register a new test customer: every field stays visible while typing, including on a screen of 5" or less.
- [ ] Log in, force-stop the app and reopen: you're still signed in (secure native refresh token).
- [ ] Airplane mode for more than 15 minutes, then back online: the session refreshes with no forced logout.
- [ ] Log out: reopening the app shows the login screen, and the old session can't be reused.

### A2. Search and keyboard
- [ ] Focus the search box: the composer stays fully above the keyboard.
- [ ] Insets: the header isn't under the status bar, and the composer isn't under the navigation bar or gesture area.
- [ ] Drag the chat with the keyboard open: the keyboard dismisses, and Send stays tappable.

Searches that should be answered without Groq (expect replies within about 1 s):
- [ ] "3 BHK under 65 lakh in Vepagunta"
- [ ] "north facing flats in Madhurawada"
- [ ] "వేపగుంటలో 65 లక్షల లోపు 3 బీహెచ్కే"
- [ ] "Madhurawada lo 2 bhk 50 lakhs lopu"

Other searches:
- [ ] Free text, which uses Groq: "a quiet home for my parents near the beach". It returns results, or a fallback message, never an error screen.
- [ ] Follow-up "under 70 lakh" keeps the locality; "start over" clears the search.
- [ ] Ask "is there a lift?" or "RERA number?": the reply says LEIA hasn't verified it, with no invented value.
- [ ] "compare listing 1 and 2" after a search: values match the listing cards (₹ in Indian grouping).
- [ ] Large system font: inputs and cards stay usable.

### A3. Property view
- [ ] Open a listing: photos, panorama and map load; "Ask" tab input stays above keyboard.
- [ ] Temple/walking question returns only listing-based info.
- [ ] Draft, rejected and sold listings never appear.

## B. Agent CRM

### B1. Sign-in and session
- [ ] Sign-in with keyboard open: password field and button visible.
- [ ] App restart keeps the staff session; sign-out revokes it.
- [ ] Customer account cannot sign in to CRM (RBAC).

### B2. Property draft form
- [ ] Create a draft with all sections; fields near the bottom (notes, multiline) scroll into view.
- [ ] Multiline text starts at the top; drag dismisses keyboard; top inset correct after scrolling.
- [ ] Validation messages for price, area, PIN code, facing (all 8 directions).
- [ ] Edit a draft from another device at the same time: the version conflict message appears, with no silent overwrite.

### B3. GPS and media
- [ ] GPS capture outdoors: accuracy shown; low-accuracy capture is refused or flagged.
- [ ] Location permission denied: manual entry path works; no crash.
- [ ] Cover photo, gallery photos and room panorama upload on mobile data; progress and errors shown.
- [ ] Submit for review → admin approves on staging → listing appears in Customer app (test listing only,
      with owner approval) → cleanup.

## C. Digital Studio (web, admin)
- [ ] `/studio/` sign-in on mobile browser and desktop.
- [ ] MP4 walkthrough upload (≤100 MiB, ≤10 min) succeeds; non-MP4 rejected (415/422).

## D. Sign-off
- [ ] All A–C items PASS on at least one Android 13+ device and one small/older device (Android 10–12).
- [ ] FAIL items have screenshots and an issue each.
