# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**LookOut** is a barangay (Philippine village) security system with three independent apps that talk to each other only over the REST API — there is no shared code or types between them:

- **`lookout_backend/`** — Django REST Framework API + AI detection pipelines (single Django app: `core`). SQLite database.
- **`lookout/`** — Vite + React 19 web dashboard for admins and dispatchers.
- **`officer_app/`** — Expo (React Native) mobile app for officers, using Expo Router.

All three read/write the same backend at `http://localhost:8000/api` (or a LAN IP for the mobile app — see below).

## Commands

### Backend (`lookout_backend/`)

```
pip install -r requirements.txt      # from repo root; a venv/ already exists at repo root
python manage.py migrate
python manage.py runserver           # serves http://localhost:8000
python manage.py seed_demo           # loads demo cameras/officers/residents/households/alerts
python manage.py enroll_faces        # builds core/vision/face_db.json from Resident.image_url photos
python manage.py enroll_faces --resident RES-05   # (re-)enroll a single resident
python manage.py watch_curfew        # runs the live webcam curfew-detection loop
python manage.py watch_curfew --debug            # same, with an OpenCV preview window
python manage.py test                # core/tests.py (currently empty/stub)
```

Requires a `.env` in `lookout_backend/` (see `.env.example`) for `EMAIL_HOST_USER`/`EMAIL_HOST_PASSWORD`/`DEFAULT_FROM_EMAIL` (Gmail SMTP, used for OTP codes). Other env vars read in `settings.py`: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `ALLOWED_CORS_ORIGINS`, `SITE_BASE_URL`, `SEMAPHORE_API_KEY`, `SEMAPHORE_SENDER_NAME`.

`watch_curfew` needs a working webcam at index 0 and the heavy CV deps (`torch`, `ultralytics`, `insightface`, `onnxruntime`, `opencv-python`) actually installed — these are large and the YOLOv8 weights (`yolov8n.pt`) and insightface `buffalo_l` pack (~280MB) auto-download on first use.

### Web dashboard (`lookout/`)

```
npm install
npm run dev        # Vite dev server, http://localhost:5173
npm run build
npm run lint        # eslint .
npm run preview
```

Reads `VITE_API_URL` (defaults to `http://localhost:8000/api`).

### Officer mobile app (`officer_app/`)

```
npm install
npm run start       # expo start --tunnel
npm run android
npm run ios
npm run web
```

Reads `EXPO_PUBLIC_API_URL`. If unset, `lib/api.ts` derives the dev machine's LAN IP from the Expo dev server's `hostUri` (native only — `localhost` on a phone means the phone itself, not the dev machine).

## Architecture

### Backend: one fat `core` app

Everything lives in `lookout_backend/core/`: `models.py`, `views.py` (function-based views + DRF `ModelViewSet`s, no separate service layer), `serializers.py`, `urls.py` (a `DefaultRouter` plus a handful of plain function-based routes for auth/settings/dashboard-stats/SMS), `permissions.py` (`IsAdmin`, `IsAdminOrReadOnly`), `throttling.py` (per-endpoint rate limits for login/OTP/password-reset).

Auth is JWT (`djangorestframework_simplejwt`) with a custom `role` field on `User`: `admin` / `dispatcher` / `officer` / `both`. The login serializer embeds `role` and `name` in the token and echoes the full user object in the login response. **Officer-vs-web access is a client-side gate, not a server-side one**: `lookout/src/api.js` rejects `role === "officer"` after a successful login, and `officer_app/lib/api.ts` rejects everything except `officer`/`both` — the backend itself doesn't stop an officer JWT from hitting other endpoints.

Business-readable codes (`CAM-01`, `OFC-01`, `RES-01`, `HH-TET-0001`, `MEM-001`, `ALT-0001`) are auto-assigned in each model's `save()` via the shared `_next_code()` helper in `models.py` — never set manually.

Two separate "person" concepts exist side by side: `Resident` (flat, used for AI face enrollment/curfew matching) and `Household`/`HouseholdMember` (structured family records, with a self-referential M2M `guardians`/`wards` on `HouseholdMember`). They are not the same table and don't reference each other.

`Alert` is the violation record. Lifecycle: `active` → `dispatched` → `resolved`, or `active`/`dispatched` → `acknowledged` (dismissed). `AlertViewSet.accept` (`POST /api/alerts/{id}/accept/`) is the one non-CRUD action: it does an atomic M2M `.add()` of the requesting officer plus a status bump, specifically to avoid two officers' concurrent accepts clobbering each other the way a client-computed PATCH of the whole `officers_assigned` list would.

There are no websockets anywhere in this system — the web dashboard (`admin_dashboard.jsx`'s `useLiveOverviewData`) and the officer app (`AssignmentContext.tsx`) both get "real-time" updates by polling `/alerts/`, `/cameras/`, `/officers/` every 4 seconds.

### AI detection pipeline (`core/vision/` + management commands)

`core/vision/recognition.py` is deliberately pure CV plumbing with **no Django model access**, so it stays importable/testable on its own. Pipeline: YOLOv8 (`ultralytics`) detects person bounding boxes in a frame → each crop goes through insightface's `FaceAnalysis` (buffalo_l/ArcFace, CPU) to get a 512-d embedding → cosine-similarity match against `core/vision/face_db.json`.

- `manage.py enroll_faces` builds `face_db.json` from `Resident.image_url` photos (downloads each image, embeds it, writes `{code, name, age, embedding}` entries).
- `manage.py watch_curfew` is the long-running detector: opens the webcam, re-loads `SystemSettings` every 5s (not every frame), and for each matched face checks age-below-`curfew_age`, current-time-within-curfew-hours, and a dwell timer (`curfew_dwell` seconds of continuous presence) before creating an `Alert` + saving an evidence JPG to `media/violations/`. A per-person `alert_cooldown` prevents re-alerting on the same person every frame.
- **Confidence scale gotcha**: insightface cosine-similarity scores for a genuine same-person match typically land around 35–70 (as a 0–100 percentage), not 90+. `SystemSettings.curfew_confidence` is calibrated to that scale (default 45) — don't reason about it as a "75% sure" bar.

Noise and waste/garbage detection are documented in `SYSTEM_FLOWCHART.md`/`FLOWCHART_COMBINED.md` as parallel pipelines (`SystemSettings` already has `noise_*`/`waste_*` fields), but unlike curfew there is no corresponding management command in `core/management/commands/` yet — only `watch_curfew` and `enroll_faces` exist.

### Web dashboard (`lookout/`)

`App.jsx` + react-router-dom only handle the outermost auth routing: `/`, `/forgot-password`, `/dashboard`. Once logged in, **all internal page navigation is client-state, not URL-based**: `admin_dashboard.jsx` holds `activePage` and switches between page components (`CameraGrid`, `AlertFeed`, `RecordsPage`, `ResidentLog`, `ResidentDatabase`, `OfficersPage`, `SystemConfig`) directly — there's no `/cameras`, `/alerts`, etc. route. Which pages are reachable per role is the `ROLE_PAGES` map at the top of `admin_dashboard.jsx`.

The access token is kept **only in a module-level JS variable** in `api.js`, never in `localStorage`/`sessionStorage` — by design, so a stored-XSS payload can't read it out of storage, and every hard page refresh forces re-login (`clearAuth()` runs at module load in `App.jsx`).

### Officer app (`officer_app/`)

Expo Router file-based routing under `app/`: `login`, `forgot-password`, `change-password`, `(tabs)/` (`index`=assignments, `history`, `profile`), `assignment/[id]`. All auth-based redirect logic is centralized in one place — `NavigationGuard` inside `app/_layout.tsx` — which reacts to `AuthContext`'s `officer`/`mustChangePassword` state; individual screens don't guard themselves.

`AssignmentContext.tsx` wraps the same `/alerts/` polling pattern as the web dashboard but reshapes `ApiAlert` into a mobile-friendly `Assignment` shape (`mapAlert`) and derives `activeAssignments` (`active`/`dispatched`) vs `historyAssignments` (`acknowledged`/`resolved`) from one alert list — there's no separate history endpoint.

Tokens persist across app restarts via `expo-secure-store` (OS Keychain/Keystore) on native, falling back to `AsyncStorage` on web (SecureStore has no web implementation).

### SMS / email integrations

- SMS uses Semaphore (Philippine SMS gateway) via `views.send_sms` — falls back to a console-log stub if `SEMAPHORE_API_KEY` is unset (dev/demo mode), so it's safe to run without credentials.
- Guardian SMS notification on curfew alerts is gated by `SystemSettings.guardian_check` and triggered manually from the web dashboard's violation modal (search households → pick numbers → edit message → send), not automatically from `watch_curfew`.
- Email (Gmail SMTP) is used only for one-time verification codes: officer/dispatcher registration email verification and forgot-password OTPs (`EmailVerificationCode` model, 10-minute expiry hardcoded as `CODE_EXPIRY_MINUTES` in `views.py`).
