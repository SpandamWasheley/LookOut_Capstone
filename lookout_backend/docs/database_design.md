# System Design: Database

## Database Design

The LookOut system is backed by a single relational schema comprising fifteen
tables, thirteen of which correspond directly to Django model classes defined
in the application's `core` app, with the remaining two existing as
automatically generated junction tables that implement many-to-many
relationships. This schema supports four functions of the system: user
authentication and role management, camera and zone administration,
automated violation detection and alerting, face-based identity matching, and
the citation workflow through which field officers formally record
violations against identified persons.

Authentication and personnel records are held in tblUser and tblOfficer.
tblUser extends Django's built-in authentication user model and stores
login credentials alongside a role designation that distinguishes
administrators, dispatchers, and officers, along with a flag used to force a
password change on next login. tblOfficer is a separate operational record
describing a person who can be dispatched to a violation and cited as the
issuing officer on a citation; it optionally links back to a tblUser account
when that officer also has system login access, but can exist independently
for officers who are tracked operationally without being given credentials.
tblEmailVerificationCode supports the account verification workflow by
recording one-time codes sent to an email address along with their
verification and consumption status.

Camera infrastructure is described by tblZone and tblCamera. A zone is
simply a named geographic grouping, and each camera optionally belongs to one
zone. The camera record carries both its administrative metadata, such as
status and frame rate, and the technical configuration needed by the
automated detectors: a stream URL for live capture, and a set of
road-edge coordinates used specifically by the parking-obstruction detector
to determine when a vehicle is encroaching on a boundary it has been shown.

Violation detection produces tblAlert records, each of which is
classified by a tblViolationType, optionally tied to the tblCamera that
observed it, and carries the media evidence produced by the detector,
including annotated and raw evidence clips. An alert may be assigned to zero
or more officers for response, a relationship realized through the explicit
junction table tblAlertOfficersAssigned. tblViolationType itself is a small
reference table currently populated with exactly four rows, corresponding to
the four violation categories the system is scoped to detect: smoking,
drinking, parking obstruction, and theft.

Facial identification is supported by tblPerson and tblFaceEmbedding.
tblPerson is an enrollment record for an individual registered into the
facial-recognition system, distinct from any notion of a household or
resident record, which the system does not maintain. Each enrolled person may
have up to three tblFaceEmbedding rows, one per captured angle, each storing
the numeric embedding vector produced by the recognition model together with
the source image and detector confidence. When an alert's captured frame is
matched against this registry, the alert is linked to the matched tblPerson
and records the match confidence achieved.

The citation workflow is built on tblViolator and tblCitation. tblViolator
represents a distinct individual who has been cited at least once; it stores
a name normalized at save time so that repeat citations against the same
typed name resolve to a single violator record without requiring a fuzzy
matching pass, and it may itself be linked to a tblPerson if the violator has
also been enrolled in the facial registry. tblCitation is the record of a
single citation event: it preserves the name exactly as entered at the time
of citation, independent of whatever tblViolator record it is later
associated with or merged into, and it is authored by an issuing tblOfficer
and, where applicable, filed by a tblUser. A citation is optionally linked to
the tblAlert that prompted it and to zero or more tblViolationType rows
describing which offenses were cited, the latter relationship realized
through the explicit junction table tblCitationViolations. tblDetectionJob
is a supporting operational table, unrelated to the citation workflow,
recording administrator-triggered test runs of a detector against an
uploaded video file rather than a live camera feed.

Finally, tblSystemSettings is a singleton configuration table, always
addressed at a single row, that holds every tunable parameter consumed by
the detection subsystem: enablement flags, confidence thresholds, and dwell
timers for each detector, together with global alerting behavior such as
cooldown periods, evidence retention, and notification channels. This table
documents its fields exactly as they exist in the current schema, including
several whose configuration groups have no corresponding implementation
elsewhere in the codebase; this is addressed as a separate note following
this section rather than in the narrative proper, so that the description of
the schema itself remains a factual account of structure rather than of
implementation status.

## Entity Relationship Diagram

The relationships among these fifteen tables are as follows. tblUser has a
one-to-one relationship with tblOfficer, since a user account may be linked
to at most one officer record and an officer record may be linked to at most
one user account, and the relationship is optional in both directions.
tblZone has a one-to-many relationship with tblCamera, since a single zone
may contain many cameras, while a camera belongs to at most one zone.
tblCamera has a one-to-many relationship with tblAlert, since a single camera
may produce many alerts over time, while an alert is associated with at most
one camera and may have none, as in the case of an alert generated from an
uploaded file rather than a live feed. tblViolationType has a one-to-many
relationship with tblAlert, since each alert is classified as exactly one
violation type, while a violation type may classify many alerts. tblAlert
has a many-to-many relationship with tblOfficer, realized through the
junction table tblAlertOfficersAssigned, since a single alert may be
assigned to several responding officers and a single officer may be assigned
to several alerts concurrently.

tblPerson has a one-to-many relationship with tblFaceEmbedding, since a
single enrolled person may have several embeddings captured from different
angles, while each embedding belongs to exactly one person. tblPerson also
has three further one-to-many relationships, each optional, reflecting its
role as a shared identity-matching target: with tblAlert, since a single
enrolled person may be the matched subject of many alerts; with tblViolator,
since a single enrolled person may correspond to many violator records; and
with tblCitation, since a single enrolled person may be the matched subject
of many citations.

tblAlert has a one-to-many relationship with tblCitation, since a single
alert may give rise to more than one citation, while a citation is founded
on at most one alert and may have none, as when a citation is filed without
a corresponding automated detection. tblViolator has a one-to-many
relationship with tblCitation, since a single violator may accumulate many
citations over time, while each citation names exactly one violator.
tblOfficer has a one-to-many relationship with tblCitation, since a single
officer may issue many citations, while each citation is issued by exactly
one officer. tblCitation has a many-to-many relationship with
tblViolationType, realized through the junction table tblCitationViolations,
since a single citation may cite several violation types at once and a
single violation type may appear on many citations. tblUser has a
one-to-many relationship with tblCitation, since a single user may file many
citations, and a further, separate one-to-many relationship with
tblDetectionJob, since a single user may trigger many detection test runs;
both relationships are optional, since the authoring user on either record
may be unset.

tblSystemSettings and tblEmailVerificationCode participate in no
foreign-key relationships with any other table in the schema: the former is
a standalone singleton of global configuration, and the latter is a
standalone record of the email verification workflow, referenced only by the
email address it was issued to rather than by a foreign key.

---

**Note for adviser discussion.** Two matters surfaced during this
documentation pass and are recorded here rather than in the narrative above,
since they concern implementation status rather than schema structure. First,
three configuration groups on tblSystemSettings — the noise-related fields,
the waste-collection-related fields, and the sms_alerts flag — have no
corresponding detector or notification implementation anywhere in the
codebase; they are documented as they exist in the schema, but represent
either unfinished or abandoned scope and should be raised for a decision on
whether to implement or remove. Second, the curfew_confidence field, despite
its name and its place among the other curfew-related fields, is actively
read by two of the detector commands (watch_all and watch_merged) as a
generic face-match confidence threshold for identifying persons in
smoking and drinking alerts; it is not used for curfew detection, which is
out of scope for this system and is not otherwise documented anywhere in
this set of documents, since it has no seeded violation type, no reachable
entry point, and no alerts in the database.
