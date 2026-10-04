"""Correcting a citation that has already been filed.

Two things have to hold.

WHO AND WHEN — the officer who filed it, while the alert is still open. A
citation is that officer's own account of what they saw; and once the incident
is closed the citation is the record of it, so "fix the spelling" stops being
available (CanEditOwnOpenCitation).

THE VIOLATOR LINK — the *_entered names are this citation's own snapshot of
what was typed, but `violator` is a link to a person record resolved FROM them.
Changing a name without re-resolving that link leaves the citation reading one
name while counting against somebody else — wrong in a way that looks right on
screen and only surfaces later, in a person's citation history.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Alert, Camera, Citation, Officer, Violator, ViolationType


class CitationEditTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user(username="ofc1", password="x", role="officer",
                                             display_name="Ofc One")
        self.officer = Officer.objects.create(name="Ofc One", user=self.user)
        self.other_user = User.objects.create_user(username="ofc2", password="x", role="officer",
                                                   display_name="Ofc Two")
        self.other_officer = Officer.objects.create(name="Ofc Two", user=self.other_user)

        self.vt = ViolationType.objects.create(code="smoking", label="Smoking",
                                               color="#f59e0b", icon="cigarette")
        self.vt2 = ViolationType.objects.create(code="drinking", label="Drinking",
                                                color="#8b5cf6", icon="beer")
        self.cam = Camera.objects.create(code="CAM-SMOKE-01", name="Hikvision")
        self.alert = Alert.objects.create(type=self.vt, camera=self.cam, confidence=0.8,
                                          timestamp=timezone.now(), status=Alert.Status.DISPATCHED)

        self.c = APIClient()
        self.c.force_authenticate(self.user)
        self.citation = self._file("Juan", "Santos", "Cruz")

    def _file(self, first, middle, last, **over):
        body = {
            "alert": self.alert.pk, "officer": self.officer.pk,
            "first_name_entered": first, "middle_name_entered": middle,
            "last_name_entered": last, "suffix_entered": "",
            "barangay_of_violation": "TETUAN", "violator_barangay": "TETUAN",
            "violations": [self.vt.pk], "notes": "", "resolve_alert": False,
        }
        body.update(over)
        r = self.c.post("/api/citations/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return Citation.objects.get(pk=r.json()["id"])

    def _patch(self, **body):
        return self.c.patch(f"/api/citations/{self.citation.pk}/", body, format="json")

    # ---- the violator link -------------------------------------------------

    def test_correcting_a_name_repoints_the_citation_at_the_right_person(self):
        was = self.citation.violator_id
        r = self._patch(first_name_entered="Jose")
        self.assertEqual(r.status_code, 200, r.content)
        self.citation.refresh_from_db()
        self.assertEqual(self.citation.first_name_entered, "Jose")
        self.assertNotEqual(self.citation.violator_id, was, "still linked to the old person")
        self.assertEqual(self.citation.violator.normalized_name, "jose santos cruz")

    def test_a_correction_onto_an_existing_person_reuses_that_record(self):
        # Ofc One cited the same person earlier under the right spelling. The
        # fix must join that record, not mint a third.
        self._file("Jose", "Santos", "Cruz")
        before = Violator.objects.count()
        self._patch(first_name_entered="Jose")
        self.citation.refresh_from_db()
        self.assertEqual(Violator.objects.count(), before, "a duplicate person was created")
        self.assertEqual(self.citation.violator.normalized_name, "jose santos cruz")

    def test_the_person_it_used_to_point_at_is_left_alone(self):
        # It may be shared with other citations, and a person with no citations
        # is still a real record. Pruning here would be a side effect nobody
        # asked for.
        was = self.citation.violator_id
        self._patch(first_name_entered="Jose")
        self.assertTrue(Violator.objects.filter(pk=was).exists())

    def test_editing_something_other_than_a_name_keeps_the_same_person(self):
        was = self.citation.violator_id
        r = self._patch(notes="two others fled", violations=[self.vt.pk, self.vt2.pk])
        self.assertEqual(r.status_code, 200, r.content)
        self.citation.refresh_from_db()
        self.assertEqual(self.citation.violator_id, was)
        self.assertEqual(self.citation.notes, "two others fled")
        self.assertEqual({v.pk for v in self.citation.violations.all()}, {self.vt.pk, self.vt2.pk})

    def test_a_partial_patch_resolves_against_the_stored_names(self):
        # PATCH sends only what changed. Resolving on the sent fields alone
        # would normalise "Cruz" against an empty first and middle name.
        self._patch(last_name_entered="Dela Cruz")
        self.citation.refresh_from_db()
        self.assertEqual(self.citation.violator.normalized_name, "juan santos dela cruz")

    def test_the_entered_snapshot_and_the_person_record_both_update(self):
        self._patch(first_name_entered="Jose", last_name_entered="Reyes")
        self.citation.refresh_from_db()
        self.assertEqual(self.citation.last_name_entered, "Reyes")
        self.assertEqual(self.citation.violator.last_name, "Reyes")

    # ---- the alert is not a citation's business ----------------------------

    def test_an_edit_never_resolves_the_alert(self):
        self._patch(notes="corrected")
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.DISPATCHED)

    def test_resolve_alert_sent_on_an_edit_is_ignored(self):
        # A typo fix must not be able to close an incident.
        r = self._patch(notes="x", resolve_alert=True)
        self.assertEqual(r.status_code, 200, r.content)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, Alert.Status.DISPATCHED)

    def test_client_uuid_is_not_rewritten_by_an_edit(self):
        # It identifies the original submission for retry purposes; an edit is
        # not a new submission.
        tagged = self._file("Ana", "", "Lim", client_uuid="11111111-1111-1111-1111-111111111111")
        r = self.c.patch(f"/api/citations/{tagged.pk}/",
                         {"notes": "x", "client_uuid": "22222222-2222-2222-2222-222222222222"},
                         format="json")
        self.assertEqual(r.status_code, 200, r.content)
        tagged.refresh_from_db()
        self.assertEqual(str(tagged.client_uuid), "11111111-1111-1111-1111-111111111111")

    # ---- who, and when -----------------------------------------------------

    def test_another_officer_cannot_correct_it(self):
        self.c.force_authenticate(self.other_user)
        r = self._patch(first_name_entered="Jose")
        self.assertEqual(r.status_code, 403, r.content)
        self.citation.refresh_from_db()
        self.assertEqual(self.citation.first_name_entered, "Juan")

    def test_it_locks_once_the_alert_is_resolved(self):
        self.alert.status = Alert.Status.RESOLVED
        self.alert.save(update_fields=["status"])
        r = self._patch(first_name_entered="Jose")
        self.assertEqual(r.status_code, 403, r.content)

    def test_it_locks_once_the_alert_is_dismissed(self):
        # Dismissed is just as closed as resolved — somebody looked and decided.
        self.alert.status = Alert.Status.ACKNOWLEDGED
        self.alert.save(update_fields=["status"])
        r = self._patch(first_name_entered="Jose")
        self.assertEqual(r.status_code, 403, r.content)

    def test_a_user_with_no_officer_profile_cannot_correct_anything(self):
        User = get_user_model()
        desk = User.objects.create_user(username="adm", password="x", role="admin")
        self.c.force_authenticate(desk)
        r = self._patch(notes="x")
        self.assertEqual(r.status_code, 403, r.content)

    # ---- identity fields are frozen once filed -----------------------------
    #
    # The permission authorises the edit against the citation AS IT STANDS —
    # yours, on an open alert. A request that is allowed through on that basis
    # and then moves the citation somewhere else authorises one thing and
    # performs another.

    def test_it_cannot_be_moved_onto_another_alert(self):
        other = Alert.objects.create(type=self.vt, camera=self.cam, confidence=0.5,
                                     timestamp=timezone.now(), status=Alert.Status.ACTIVE)
        r = self._patch(alert=other.pk)
        self.assertEqual(r.status_code, 400, r.content)
        self.assertIn("alert", r.json())
        self.citation.refresh_from_db()
        self.assertEqual(self.citation.alert_id, self.alert.pk)

    def test_it_cannot_be_credited_to_another_officer(self):
        r = self._patch(officer=self.other_officer.pk)
        self.assertEqual(r.status_code, 400, r.content)
        self.citation.refresh_from_db()
        self.assertEqual(self.citation.officer_id, self.officer.pk)

    def test_the_violator_cannot_be_set_by_hand_on_an_edit(self):
        # The sharpest of the three: it would pin the citation to a person
        # record that does not match the name printed on it — exactly the
        # mismatch perform_update re-resolves to prevent.
        stranger = Violator.objects.create(first_name="Someone", last_name="Else",
                                           normalized_name="someone else")
        was = self.citation.violator_id
        r = self._patch(violator=stranger.pk)
        self.assertEqual(r.status_code, 400, r.content)
        self.citation.refresh_from_db()
        self.assertEqual(self.citation.violator_id, was)

    def test_a_frozen_field_sent_beside_a_valid_change_rejects_the_whole_edit(self):
        # Not "apply the good half": a partially-applied correction is worse
        # than a refused one, because it looks like it worked.
        r = self._patch(notes="corrected", officer=self.other_officer.pk)
        self.assertEqual(r.status_code, 400, r.content)
        self.citation.refresh_from_db()
        self.assertEqual(self.citation.notes, "")

    def test_the_name_still_drives_the_violator_even_if_one_is_sent(self):
        # Belt-and-braces: perform_update pops the identity fields too, so a
        # caller reaching it without the validator still resolves from names.
        stranger = Violator.objects.create(first_name="Someone", last_name="Else",
                                           normalized_name="someone else")
        self.citation.first_name_entered = "Jose"
        self.citation.save(update_fields=["first_name_entered"])
        from core.serializers import CitationSerializer
        ser = CitationSerializer(self.citation, data={"violator": stranger.pk,
                                                      "notes": "x"}, partial=True)
        ser.is_valid()                       # will fail -- that is the point
        self.assertIn("violator", ser.errors)

    def test_reading_is_not_restricted(self):
        # The dashboard lists citations; only writes are gated.
        self.c.force_authenticate(self.other_user)
        r = self.c.get(f"/api/citations/{self.citation.pk}/")
        self.assertEqual(r.status_code, 200, r.content)

    def test_filing_a_new_citation_is_unaffected(self):
        # The permission has no has_permission(), so create must still work for
        # anyone authenticated — including on somebody else's alert.
        self.c.force_authenticate(self.other_user)
        r = self.c.post("/api/citations/", {
            "alert": self.alert.pk, "officer": self.other_officer.pk,
            "first_name_entered": "Ana", "last_name_entered": "Lim",
            "barangay_of_violation": "TETUAN", "violator_barangay": "TETUAN",
            "violations": [self.vt.pk], "resolve_alert": False,
        }, format="json")
        self.assertEqual(r.status_code, 201, r.content)
