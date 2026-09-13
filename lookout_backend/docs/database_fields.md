# Database Fields

Generated from `core/models.py` as of 2026-09-10. 15 tables total: 13 model-backed
tables plus 2 explicit many-to-many junction tables (`tblAlertOfficersAssigned`,
`tblCitationViolations`). Two Django-auth junction tables (`core_user_groups`,
`core_user_user_permissions`) are framework plumbing and are intentionally
omitted, per scope decision. The `curfew` violation type is dead code (no
entry point, no seeded `ViolationType` row, zero alerts) and is not documented
here or anywhere else in this set.

---

## TABLE tblUser
*(`core_user` — extends Django's `AbstractUser`; inherited columns are real
columns in this table and are listed alongside the custom ones.)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| password | VARCHAR | 128 | | No |
| last_login | DATETIME | - | | Yes |
| is_superuser | BOOLEAN | - | | No |
| username | VARCHAR | 150 | Unique | No |
| first_name | VARCHAR | 150 | | No |
| last_name | VARCHAR | 150 | | No |
| email | VARCHAR | 254 | | No |
| is_staff | BOOLEAN | - | | No |
| is_active | BOOLEAN | - | | No |
| date_joined | DATETIME | - | | No |
| role | VARCHAR | 20 | | No |
| display_name | VARCHAR | 150 | | No |
| must_change_password | BOOLEAN | - | | No |

---

## TABLE tblEmailVerificationCode
*(`core_emailverificationcode`)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| email | VARCHAR | 254 | | No |
| code | VARCHAR | 6 | | No |
| created_at | DATETIME | - | | No |
| verified | BOOLEAN | - | | No |
| used | BOOLEAN | - | | No |

---

## TABLE tblZone
*(`core_zone`)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| name | VARCHAR | 100 | Unique | No |

---

## TABLE tblViolationType
*(`core_violationtype`)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| code | VARCHAR | 30 | Unique | No |
| label | VARCHAR | 100 | | No |
| color | VARCHAR | 7 | | No |
| icon | VARCHAR | 10 | | No |

---

## TABLE tblCamera
*(`core_camera`)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| code | VARCHAR | 20 | Unique | No |
| name | VARCHAR | 150 | | No |
| zone_id | INTEGER | - | Foreign Key | Yes |
| status | VARCHAR | 10 | | No |
| fps | SMALLINT | - | | No |
| last_motion_at | DATETIME | - | | Yes |
| image_url | VARCHAR | 200 | | No |
| stream_url | VARCHAR | 500 | | No |
| edges | TEXT | - | | No |
| edges_width | INTEGER | - | | Yes |
| edges_height | INTEGER | - | | Yes |
| obstruction_pct | SMALLINT | - | | No |
| obstruction_minutes | FLOAT | - | | No |

---

## TABLE tblOfficer
*(`core_officer`)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| code | VARCHAR | 20 | Unique | No |
| user_id | INTEGER | - | Foreign Key | Yes |
| name | VARCHAR | 150 | | No |
| badge | VARCHAR | 20 | | No |
| status | VARCHAR | 15 | | No |
| location | VARCHAR | 100 | | No |
| phone | VARCHAR | 30 | | No |
| shift | VARCHAR | 50 | | No |
| joined_date | DATE | - | | Yes |

`user_id` is also unique at the database level (one-to-one to `tblUser`); the
Key Type column above records it as Foreign Key per this document's fixed
vocabulary — see `database_design.md` for the one-to-one cardinality.

---

## TABLE tblPerson
*(`core_person` — a face-registry enrollment record, not a resident/household
record.)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| person_code | VARCHAR | 20 | Unique | No |
| full_name | VARCHAR | 150 | | No |
| status | VARCHAR | 10 | | No |
| enrolled_at | DATETIME | - | | Yes |
| notes | TEXT | - | | No |
| created_at | DATETIME | - | | No |

---

## TABLE tblFaceEmbedding
*(`core_faceembedding` — one biometric template per enrolled angle of a
`tblPerson`.)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| person_id | INTEGER | - | Foreign Key | No |
| angle | VARCHAR | 10 | | No |
| image | VARCHAR | 100 | | No |
| embedding | TEXT | - | | No |
| det_score | FLOAT | - | | Yes |
| created_at | DATETIME | - | | No |

A composite unique constraint on (`person_id`, `angle`) also applies — not
representable as a single Key Type value above.

---

## TABLE tblAlert
*(`core_alert`)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| code | VARCHAR | 20 | Unique | No |
| type_id | INTEGER | - | Foreign Key | No |
| status | VARCHAR | 15 | | No |
| camera_id | INTEGER | - | Foreign Key | Yes |
| timestamp | DATETIME | - | | No |
| confidence | FLOAT | - | | No |
| description | TEXT | - | | No |
| image_url | VARCHAR | 200 | | No |
| video_url | VARCHAR | 200 | | No |
| raw_video_url | VARCHAR | 200 | | No |
| suspect | VARCHAR | 150 | | No |
| notes | TEXT | - | | No |
| matched_person_id | INTEGER | - | Foreign Key | Yes |
| match_confidence | FLOAT | - | | Yes |

---

## TABLE tblAlertOfficersAssigned
*(`core_alert_officers_assigned` — junction table for the `tblAlert` ↔
`tblOfficer` many-to-many relationship; auto-generated by Django, no model
class of its own.)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| alert_id | INTEGER | - | Foreign Key | No |
| officer_id | INTEGER | - | Foreign Key | No |

---

## TABLE tblViolator
*(`core_violator`)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| first_name | VARCHAR | 100 | | No |
| last_name | VARCHAR | 100 | | No |
| middle_name | VARCHAR | 100 | | No |
| suffix | VARCHAR | 5 | | No |
| normalized_name | VARCHAR | 310 | | No |
| matched_person_id | INTEGER | - | Foreign Key | Yes |
| aliases | TEXT | - | | No |
| first_seen | DATETIME | - | | No |
| last_seen | DATETIME | - | | Yes |

`normalized_name` is indexed (non-unique) and computed automatically on save;
it is not user-editable.

---

## TABLE tblCitation
*(`core_citation`)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| alert_id | INTEGER | - | Foreign Key | Yes |
| violator_id | INTEGER | - | Foreign Key | No |
| first_name_entered | VARCHAR | 100 | | No |
| last_name_entered | VARCHAR | 100 | | No |
| middle_name_entered | VARCHAR | 100 | | No |
| suffix_entered | VARCHAR | 5 | | No |
| officer_id | INTEGER | - | Foreign Key | No |
| barangay_of_violation | VARCHAR | 30 | | No |
| violator_barangay | VARCHAR | 50 | | No |
| matched_person_id | INTEGER | - | Foreign Key | Yes |
| match_confidence | FLOAT | - | | Yes |
| notes | TEXT | - | | No |
| created_by_id | INTEGER | - | Foreign Key | Yes |
| created_at | DATETIME | - | | No |
| client_uuid | VARCHAR | 32 | Unique | Yes |

---

## TABLE tblCitationViolations
*(`core_citation_violations` — junction table for the `tblCitation` ↔
`tblViolationType` many-to-many relationship; auto-generated by Django, no
model class of its own.)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| citation_id | INTEGER | - | Foreign Key | No |
| violationtype_id | INTEGER | - | Foreign Key | No |

---

## TABLE tblDetectionJob
*(`core_detectionjob` — an admin-triggered test run of one detector against an
uploaded video file; a testing/demo tool, not part of the live-camera
pipeline.)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| violation_type | VARCHAR | 20 | | No |
| source_filename | VARCHAR | 255 | | No |
| source_path | VARCHAR | 500 | | No |
| status | VARCHAR | 10 | | No |
| pid | INTEGER | - | | Yes |
| started_at | DATETIME | - | | No |
| finished_at | DATETIME | - | | Yes |
| error | TEXT | - | | No |
| created_by_id | INTEGER | - | Foreign Key | Yes |

---

## TABLE tblSystemSettings
*(`core_systemsettings` — a singleton configuration row, always `id = 1`,
read/written through `SystemSettings.load()`.)*

| Attribute Name | Data Type | Max Length | Key Type | Null |
|---|---|---|---|---|
| id | INTEGER | - | Primary Key | No |
| curfew_start | TIME | - | | No |
| curfew_end | TIME | - | | No |
| curfew_age | SMALLINT | - | | No |
| curfew_confidence | SMALLINT | - | | No |
| curfew_dwell | SMALLINT | - | | No |
| guardian_check | BOOLEAN | - | | No |
| unknown_alert | BOOLEAN | - | | No |
| noise_enabled | BOOLEAN | - | | No |
| noise_threshold_db | SMALLINT | - | | No |
| noise_duration | SMALLINT | - | | No |
| waste_enabled | BOOLEAN | - | | No |
| waste_confidence | SMALLINT | - | | No |
| waste_dwell | SMALLINT | - | | No |
| waste_collection_start | TIME | - | | No |
| waste_collection_end | TIME | - | | No |
| parking_enabled | BOOLEAN | - | | No |
| parking_confidence | SMALLINT | - | | No |
| parking_dwell | SMALLINT | - | | No |
| parking_move_tolerance | SMALLINT | - | | No |
| smoking_enabled | BOOLEAN | - | | No |
| smoking_confidence | SMALLINT | - | | No |
| smoking_dwell | SMALLINT | - | | No |
| thief_enabled | BOOLEAN | - | | No |
| thief_confidence | SMALLINT | - | | No |
| thief_dwell | SMALLINT | - | | No |
| drinking_enabled | BOOLEAN | - | | No |
| drinking_confidence | SMALLINT | - | | No |
| drinking_dwell | SMALLINT | - | | No |
| drinking_hours_enabled | BOOLEAN | - | | No |
| drinking_start | TIME | - | | No |
| drinking_end | TIME | - | | No |
| drinking_min_group | SMALLINT | - | | No |
| drinking_group_duration | SMALLINT | - | | No |
| drinking_held_dwell | SMALLINT | - | | No |
| drinking_evidence_max_age | SMALLINT | - | | No |
| drinking_mouth_proximity | FLOAT | - | | No |
| drinking_cooldown_center_dist | FLOAT | - | | No |
| alert_cooldown | SMALLINT | - | | No |
| evidence_retention_days | SMALLINT | - | | No |
| auto_dispatch | BOOLEAN | - | | No |
| email_alerts | BOOLEAN | - | | No |
| sms_alerts | BOOLEAN | - | | No |
| updated_at | DATETIME | - | | No |

**Note for adviser discussion.** This table's 43 configuration fields (44
including `id`) are documented here exactly as they exist, without deletion or
omission, but three groups have no corresponding implementation in the
codebase and should be flagged as scope questions: the `noise_*` fields (3)
and `waste_*` fields (5) have no `watch_noise`/`watch_waste` management
command anywhere in the project — no detector reads them; `sms_alerts` is a
boolean toggle with no SMS-sending code anywhere in the codebase (no provider
integration, no send function). Separately, `curfew_confidence` — despite its
name and its home in the curfew field group — is actively read by
`watch_all.py` and `watch_merged.py`, where it is passed as the generic
face-match confidence threshold when checking smoking/drinking track
detections against the enrolled `tblPerson`/`tblFaceEmbedding` registry. It is
not used for curfew detection (curfew itself is out of scope and undocumented
here); the field is simply being repurposed under its original name. This is
flagged for awareness only — no rename is being made.
