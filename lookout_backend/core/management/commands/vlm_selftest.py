"""Make ONE real VLM call and report exactly what came back.

Why this exists as its own command: `build_verifier` only checks that a key is
non-empty and that the client constructs. It cannot tell a good key from a bad
one, because nothing is sent. So every watcher prints "VLM verification: ON" for
a malformed or revoked key, and the real failure only surfaces later as a stats
line during an actual alert -- which is a terrible place to discover it.

This command sends one request and prints the outcome, so a key can be verified
in five seconds instead of by waiting for somebody to light a cigarette.

    python manage.py vlm_selftest
    python manage.py vlm_selftest --kind holdup --image media/violations/x.jpg

It prints no part of the key, so the output is safe to paste into a chat or a
report.
"""

import os

import numpy as np
from django.core.management.base import BaseCommand

from core.models import SystemSettings
from core.vision import vlm


class Command(BaseCommand):
    help = "Make one real VLM call and report whether the key and model work."

    def add_arguments(self, parser):
        parser.add_argument(
            "--kind", default="smoking",
            choices=sorted(vlm.SPECS),
            help="Which prompt spec to send (default: smoking).")
        parser.add_argument(
            "--image", default=None,
            help="Path to a JPG/PNG to send. Omitted: a synthetic grey frame, "
                 "which is enough to test auth and the schema (expect the "
                 "model to answer 'unclear', which is a PASS).")
        parser.add_argument(
            "--benchmark", action="store_true",
            help="Time several image/size configurations and recommend one for "
                 "this machine. Makes several real calls, so it takes a while "
                 "on CPU-only hardware -- which is precisely the case it "
                 "exists to measure.")
        parser.add_argument(
            "--model", default=None,
            help="Override the model id from Settings, to test a different one "
                 "without saving it.")

    def handle(self, *args, **options):
        cfg = SystemSettings.load()
        model = options["model"] or cfg.vlm_model

        # --- where the key is coming from, without revealing it -------------
        self.stdout.write(self.style.MIGRATE_HEADING("Configuration"))
        if cfg.vlm_api_key:
            source, key = "SystemSettings (dashboard)", cfg.vlm_api_key
        else:
            env = (os.environ.get("GOOGLE_API_KEY")
                   or os.environ.get("GEMINI_API_KEY") or "")
            source, key = ("environment" if env else "nowhere"), env

        self.stdout.write(f"  enabled   : {cfg.vlm_enabled}")
        self.stdout.write(f"  provider  : {cfg.vlm_provider}")
        self.stdout.write(f"  model     : {model}")
        if cfg.vlm_provider == "ollama":
            # v3 runs locally, so there is no credential to check. What matters
            # instead is whether the server is up and the model is pulled --
            # build_verifier answers both, below.
            self.stdout.write(f"  endpoint  : {cfg.vlm_endpoint}")
            self.stdout.write("  key       : not needed (runs locally)")
        elif key:
            self.stdout.write(f"  key shape : {key[:4]}... ({len(key)} chars)")
        else:
            self.stdout.write(self.style.ERROR(
                "  no key, and this provider needs one."))

        verifier = vlm.build_verifier(
            enabled=cfg.vlm_enabled, provider=cfg.vlm_provider,
            api_key=cfg.vlm_api_key or None, model=model,
            timeout=cfg.vlm_timeout, endpoint=cfg.vlm_endpoint)

        self.stdout.write("")
        self.stdout.write(vlm.describe(verifier, model))
        if isinstance(verifier, vlm.DisabledVerifier):
            self.stdout.write(self.style.ERROR(
                "\nNothing was sent -- the verifier is inert, so there is "
                "nothing to test. Fix the reason above and run this again."))
            return

        # --- the actual call ------------------------------------------------
        frame = self._load_frame(options["image"])
        if frame is None:
            return

        if options["benchmark"]:
            return self._benchmark(verifier, frame, options["kind"], cfg)

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Sending one {options['kind']} request..."))
        verdict = vlm.verify_frame(
            verifier, frame, options["kind"],
            box=None if options["image"] else (10, 10, 190, 290),
            context="self-test, no detector involved")

        self.stdout.write("")
        if verdict.ok:
            self.stdout.write(self.style.SUCCESS("PASS -- the API answered."))
            self.stdout.write(f"  verdict   : {verdict.verdict}")
            self.stdout.write(f"  confidence: {verdict.confidence:.2f}")
            self.stdout.write(f"  reason    : {verdict.reason}")
            self.stdout.write(f"  latency   : {verdict.latency:.2f}s")
            for name, value in sorted(verdict.cues.items()):
                self.stdout.write(f"  {name:<24}: {value}")
            if verdict.verdict == vlm.UNCLEAR and not options["image"]:
                self.stdout.write(
                    "\n'unclear' on a blank grey frame is the CORRECT answer. "
                    "Auth, the schema and parsing all work. Pass --image with "
                    "real footage to test the judgement itself.")
        else:
            self.stdout.write(self.style.ERROR("FAIL -- no usable answer."))
            self.stdout.write(f"  error: {verdict.error}")
            self.stdout.write("")
            self.stdout.write(self._diagnose(verdict.error))
            self.stdout.write(self.style.WARNING(
                "\nDetection still works. Every failure here is fail-open: the "
                "watchers publish alerts on the geometry alone."))

    # ---------------------------------------------------------------- helpers
    # Cheapest first, so a machine that cannot manage even one small image
    # reports that in a minute rather than after a long wait on the heaviest
    # configuration. Each row is (label, scene?, crops, longest edge).
    BENCH_CONFIGS = (
        ("minimum   1 crop  @512",  False, 1, 512),
        ("light     1+1     @512",  True,  1, 512),
        ("moderate  1+2     @768",  True,  2, 768),
        ("v3 default 1+3    @1024", True,  3, 1024),
    )

    # Above this a call is not worth making on the alert path: the frame loop
    # is blocked for the duration, and an officer waiting on a verdict that
    # arrives a minute late would have been better served by the geometry.
    USABLE_SECONDS = 20.0

    def _benchmark(self, verifier, frame, kind, cfg):
        """Time each configuration and recommend the richest usable one."""
        import numpy as np

        self.stdout.write(self.style.MIGRATE_HEADING(
            "\nBenchmark -- several real calls, slowest last"))
        self.stdout.write(
            "  Each row sends a different number of images at a different "
            "size.\n  Prefill dominates, so both matter.\n")

        # A buffer of distinct frames, so the crop count is actually exercised
        # rather than the same image being sent three times.
        buf = vlm.FrameBuffer(count=3, spacing=0.0)
        for i in range(3):
            shifted = np.roll(frame, i * 7, axis=1)
            buf.add(shifted, now=float(i))

        h, w = frame.shape[:2]
        box = (int(w * 0.1), int(h * 0.1), int(w * 0.9), int(h * 0.9))

        results = []
        for label, scene, crops, edge in self.BENCH_CONFIGS:
            images = (1 if scene else 0) + crops
            self.stdout.write(f"  {label:<24} {images} image(s) ... ", ending="")
            self.stdout.flush()
            verdict = vlm.verify_frame(
                verifier, frame, kind, box=box,
                frames=buf.recent(crops)[:max(crops - 1, 0)],
                context="benchmark",
                max_edge=edge, send_scene=scene, max_images=images)
            if not verdict.ok:
                self.stdout.write(self.style.ERROR(f"FAILED ({verdict.error[:40]})"))
                results.append((label, None, images, edge, scene, crops))
                continue
            secs = verdict.latency or 0.0
            style = self.style.SUCCESS if secs <= self.USABLE_SECONDS else self.style.WARNING
            self.stdout.write(style(f"{secs:6.1f}s"))
            results.append((label, secs, images, edge, scene, crops))

        self.stdout.write("")
        ok = [r for r in results if r[1] is not None and r[1] <= self.USABLE_SECONDS]
        if not ok:
            fastest = min((r for r in results if r[1] is not None),
                          key=lambda r: r[1], default=None)
            self.stdout.write(self.style.ERROR(
                "No configuration came in under "
                f"{self.USABLE_SECONDS:.0f}s."))
            if fastest:
                self.stdout.write(
                    f"The fastest was {fastest[0].strip()} at {fastest[1]:.0f}s, "
                    "which is still too slow to sit in the alert path.")
            self.stdout.write(
                "\nThis machine cannot run the checker at a usable speed. The "
                "options, best first:\n"
                "  1. Run Ollama on a machine with an NVIDIA GPU and point\n"
                "     vlm_endpoint at it. Frames stay on your LAN, so the\n"
                "     privacy argument is unaffected.\n"
                "  2. Try a smaller model: ollama pull qwen2.5-vl:3b\n"
                "  3. Leave the checker off. Every detector already runs\n"
                "     without it -- that is what fail-open means.")
            return

        # The richest usable configuration: most images, then largest edge.
        best = max(ok, key=lambda r: (r[2], r[3]))
        self.stdout.write(self.style.SUCCESS(
            f"Recommended: {best[0].strip()} at {best[1]:.1f}s"))
        self.stdout.write("Apply it with:\n")
        self.stdout.write(
            f"  python manage.py shell -c \"from core.models import "
            f"SystemSettings as S; c=S.load(); "
            f"c.vlm_send_scene={best[4]}; c.vlm_frames={best[5]}; "
            f"c.vlm_max_edge={best[3]}; c.save()\"")
        if best[3] < 1024 or not best[4]:
            self.stdout.write(self.style.WARNING(
                "\nThis is below the v3 default, so report the trade honestly: "
                "fewer or smaller images means the motion questions have less "
                "to compare and small objects are harder to see. The accuracy "
                "cost is real and should be measured, not assumed away."))

    def _load_frame(self, path):
        if path is None:
            # Mid-grey rather than black: a pure-zero frame is a plausible
            # trigger for a "too dark to judge" refusal, which would look like
            # a failure and is not one.
            return np.full((300, 200, 3), 120, dtype=np.uint8)
        try:
            import cv2
        except ImportError:
            self.stdout.write(self.style.ERROR("opencv is not installed."))
            return None
        frame = cv2.imread(path)
        if frame is None:
            self.stdout.write(self.style.ERROR(f"could not read {path!r}"))
            return None
        self.stdout.write(f"  image     : {path} {frame.shape[1]}x{frame.shape[0]}")
        return frame

    @staticmethod
    def _diagnose(error):
        """Turn the provider's error into the thing to actually go and do."""
        low = (error or "").lower()
        if "api key not valid" in low or "api_key_invalid" in low or "400" in low:
            return ("The key was rejected -- malformed, revoked, or copied "
                    "with a stray space or newline. Create a fresh one at "
                    "https://aistudio.google.com/apikey and use the 'Copy "
                    "key' button rather than selecting the text by hand.")
        if "401" in low or "403" in low or "permission" in low:
            return ("Authenticated but not allowed. Usually an OAuth token "
                    "used where an API key belongs, or the Generative Language "
                    "API not enabled on the project. A key from AI Studio "
                    "needs no project setup.")
        if "not found" in low or "404" in low:
            return ("Ollama does not have this model. Almost always it is "
                    "still downloading, or the pull was interrupted -- check "
                    "with `ollama list`, and re-run `ollama pull qwen3-vl:4b` "
                    "if it is missing. A partially downloaded model shows no "
                    "entry in the list at all.")
        if "429" in low or "resource_exhausted" in low or "quota" in low:
            return ("Rate limited or out of free quota. The key works. Wait, "
                    "or raise the quota.")
        if "timeout" in low or "deadline" in low:
            return ("Timed out before an answer. Raise vlm_timeout in "
                    "Settings, or check the connection.")
        if "503" in low or "unavailable" in low or "high demand" in low:
            return ("Google's side is busy, not yours. The key and model are "
                    "fine -- this is exactly what fail-open is for. Retry.")
        if "readerror" in low or "10054" in low or "connection" in low:
            return ("The connection dropped mid-request. Transient; retry. If "
                    "it happens on most calls, check the network or a proxy.")
        if "not reachable" in low:
            return ("Ollama is not running. Start it with `ollama serve`, then "
                    "run this again.")
        if "not pulled" in low:
            return ("The model is not downloaded yet. Run: "
                    "ollama pull qwen3-vl:4b")
        if "empty response" in low:
            return ("The model returned nothing -- a safety filter or a "
                    "truncated generation. The key works. Try --image with a "
                    "different frame.")
        return ("Unexpected. Paste this whole output when asking for help -- it "
                "contains no part of the key.")
