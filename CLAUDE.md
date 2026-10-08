# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**LookOut** is a barangay (Philippine village) security system with three independent apps that talk to each other only over the REST API — there is no shared code or types between them:

- **`lookout_backend/`** — Django REST Framework API + AI detection pipelines (single Django app: `core`). SQLite database.
- **`lookout/`** — Vite + React 19 web dashboard for admins and dispatchers.
- **`officer_app/`** — Expo (React Native) mobile app for officers, using Expo Router.

All three read/write the same backend at `http://localhost:8000/api` (or a LAN IP for the mobile app — see below).

## Scope (read first)

- **LookOut is a SINGLE-camera system** (one Hikvision DS-2CD1047G2, `Camera` code `CAM-SMOKE-01`, shown as "Hikvision DS-2CD1047G2"). Never add UI, seed data or defaults that imply multiple cameras. All `watch_*` commands default to `--camera CAM-SMOKE-01`.
- **Violation types in scope: parking obstruction, smoking, holdup (theft), drinking — nothing else.** Curfew, noise, waste and SMS/Semaphore are permanently out of scope: do not re-add them to the UI, commands or docs. The `curfew_*`/`noise_*`/`waste_*`/`guardian_check`/`unknown_alert`/`sms_alerts`/`email_alerts`/`auto_dispatch` settings columns, the `/sms/send/` endpoint and the household/resident models have been removed; do not re-add them.
- **Facial recognition has been removed from LookOut and must not be re-added** — no face detection/matching, embeddings, enrollment, resident-matching or "matched person"/unknown-person features, in any layer (backend, web dashboard, officer app). Detection is object/behaviour-based only.
- **"-TEST" cameras** (`CAM-<TYPE>-TEST`) are created by the Run Detection upload path to tag alerts from uploaded footage (used for accuracy evaluation). They must keep that suffix; `CameraViewSet` and `dashboard_stats` hide them from every camera list, but their alerts remain visible and filterable.
- The Live Feeds layout defaults to 1×1 on every visit and is intentionally not persisted.

## Commands

### Backend (`lookout_backend/`)

```
pip install -r requirements.txt      # from repo root; a venv/ already exists at repo root
python manage.py migrate
python manage.py runserver           # serves http://localhost:8000
python manage.py seed_demo           # loads demo users/officers/alerts (no cameras — single-camera system)
python manage.py watch_all --source <rtsp-url>   # all four detectors on the one camera (also: watch_smoking / watch_drinking / watch_parking / watch_thief)
python manage.py test                # core/tests.py (currently empty/stub)
```

Requires a `.env` in `lookout_backend/` (see `.env.example`) for `BREVO_API_KEY`/`DEFAULT_FROM_EMAIL` (OTP codes, sent via the Brevo HTTP API — see `core/mail.py`; the `EMAIL_HOST_*` SMTP vars are only read if `EMAIL_BACKEND` is pointed back at Django's SMTP backend). Other env vars read in `settings.py`: `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `ALLOWED_CORS_ORIGINS`, `SITE_BASE_URL`.

The `watch_*` detectors need the heavy CV deps (`torch`, `ultralytics`, `opencv-python`, …) installed (`requirements-detection.txt` on a workstation, `requirements-detection-server.txt` on a server — the latter drops `opencv-python`, which needs `libGL.so.1`); the YOLO weights load from `core/vision/`. Whether a given process may launch a detector at all is the `DETECTION_ENABLED` setting; a hosted deployment that runs them reaches the camera and Ollama over tunnels (`STREAM_URL` / the camera's stream URL, and `VLM_ENDPOINT`) — see `render.yaml` and `ngrok.example.yml`.

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

Everything lives in `lookout_backend/core/`: `models.py`, `views.py` (function-based views + DRF `ModelViewSet`s, no separate service layer), `serializers.py`, `urls.py` (a `DefaultRouter` plus a handful of plain function-based routes for auth/settings/dashboard-stats), `permissions.py` (`IsAdmin`, `IsAdminOrReadOnly`), `throttling.py` (per-endpoint rate limits for login/OTP/password-reset).

Auth is JWT (`djangorestframework_simplejwt`) with a custom `role` field on `User`: `admin` / `dispatcher` / `officer` / `both`. The login serializer embeds `role` and `name` in the token and echoes the full user object in the login response. Login returns **both** an access token (30 min) and a refresh token (7 days); both clients redeem the refresh token against `/api/auth/refresh/` on a 401 and retry the request once, each behind a single-flight guard so the 4-second polling can't fire parallel refreshes. The `role`/`name` claims survive a refresh. **Officer-vs-web access is a client-side gate, not a server-side one**: `lookout/src/api.js` rejects `role === "officer"` after a successful login, and `officer_app/lib/api.ts` rejects everything except `officer`/`both` — the backend itself doesn't stop an officer JWT from hitting other endpoints.

Business-readable codes (`CAM-01`, `OFC-01`, `ALT-0001`) are auto-assigned in each model's `save()` via the shared `_next_code()` helper in `models.py` — never set manually.

`Alert` is the violation record. Lifecycle: `active` → `dispatched` → `resolved`, or `active`/`dispatched` → `acknowledged` (dismissed). `AlertViewSet.accept` (`POST /api/alerts/{id}/accept/`) is the one non-CRUD action: it does an atomic M2M `.add()` of the requesting officer plus a status bump, specifically to avoid two officers' concurrent accepts clobbering each other the way a client-computed PATCH of the whole `officers_assigned` list would.

There are no websockets anywhere in this system — the web dashboard (`admin_dashboard.jsx`'s `useLiveOverviewData`) and the officer app (`AssignmentContext.tsx`) both get "real-time" updates by polling `/alerts/`, `/cameras/`, `/officers/` every 4 seconds.

### AI detection pipeline (`core/vision/` + management commands)

`core/vision/recognition.py` is deliberately pure CV plumbing with **no Django model access**, so it stays importable/testable on its own. Pipeline: YOLOv8 (`ultralytics`) detects people and the violation objects (one merged model, `MODEL_PATH`); YOLOv8-pose supplies the keypoints for the mouth anchor (`find_mouth_pose`) and the hand-to-mouth gesture. There is no face recognition.

- The four in-scope detectors are `manage.py watch_smoking`, `watch_drinking`, `watch_parking` and `watch_thief` (plus `watch_all`/`watch_merged` which run several). Each re-loads `SystemSettings` every few seconds and creates an `Alert` with evidence saved under `media/violations/`. Thresholds are edited in Settings (Parking, Smoking, Holdup, Drinking panels).

**Uploading a clip for a run is chunked and resumable, and must stay that way** — the source footage is routinely 200 MB, and a single multipart POST of that size times out through a tunnel and loses every byte. `UploadSessionViewSet` (`/api/uploads/`) takes a declared filename+size, then the pieces (`UploadSession.CHUNK_BYTES`, 5 MiB) into `media/uploads/partial/<id>/`, and `complete/` stitches them into an ordinary `media/uploads/` clip returning the same `staged_token` payload as `DetectionJobViewSet.frame` — so nothing downstream knows how the bytes arrived. Which chunks landed is read off the part directory, never stored in a column, so a resume cannot trust a counter the disk disagrees with. The driver is `lookout/src/chunkedUpload.js` (3 in flight, per-chunk retry); re-declaring the same file (name|size|lastModified fingerprint) returns the unfinished session instead of a new one, which is what makes a dropped request — or a page refresh — resume rather than restart. Abandoned sessions are swept by `_purge_stale_uploads`.

### Web dashboard (`lookout/`)

`App.jsx` + react-router-dom only handle the outermost auth routing: `/`, `/forgot-password`, `/dashboard`. Once logged in, **all internal page navigation is client-state, not URL-based**: `admin_dashboard.jsx` holds `activePage` and switches between page components (`CameraGrid`, `AlertFeed`, `RecordsPage`, `ResidentLog`, `RunDetectionPage`, `OfficersPage`, `SystemConfig`) directly — there's no `/cameras`, `/alerts`, etc. route. Which pages are reachable per role is the `ROLE_PAGES` map at the top of `admin_dashboard.jsx`.

Both the access token **and the refresh token** are kept **only in module-level JS variables** in `api.js`, never in `localStorage`/`sessionStorage` — by design, so a stored-XSS payload can't read either out of storage, and every hard page refresh forces re-login (`clearAuth()` runs at module load in `App.jsx`). The refresh token buys mid-session survival (a 30-minute access token would otherwise eject a dispatcher twice a shift), **not** persistence across reloads; persisting it would reintroduce exactly the long-lived stealable credential this avoids. The officer app is different on purpose — it persists both in `expo-secure-store`, because an app being killed and reopened is normal phone behaviour, not a red flag.

### Officer app (`officer_app/`)

Expo Router file-based routing under `app/`: `login`, `forgot-password`, `change-password`, `(tabs)/` (`index`=assignments, `history`, `profile`), `assignment/[id]`. All auth-based redirect logic is centralized in one place — `NavigationGuard` inside `app/_layout.tsx` — which reacts to `AuthContext`'s `officer`/`mustChangePassword` state; individual screens don't guard themselves.

`AssignmentContext.tsx` wraps the same `/alerts/` polling pattern as the web dashboard but reshapes `ApiAlert` into a mobile-friendly `Assignment` shape (`mapAlert`) and derives `activeAssignments` (`active`/`dispatched`) vs `historyAssignments` (`acknowledged`/`resolved`) from one alert list — there's no separate history endpoint.

Tokens persist across app restarts via `expo-secure-store` (OS Keychain/Keystore) on native, falling back to `AsyncStorage` on web (SecureStore has no web implementation).

### Email integration

- Email (Brevo's HTTP API, via the `core.mail.BrevoAPIBackend` email backend — not SMTP, which
  managed hosts commonly block) is used only for one-time verification codes: officer/dispatcher registration email verification and forgot-password OTPs (`EmailVerificationCode` model, 10-minute expiry hardcoded as `CODE_EXPIRY_MINUTES` in `views.py`).
