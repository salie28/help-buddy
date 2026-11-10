import json
import os
import time

from helpme import config
from helpme.history import INTERRUPTED, History, cleanup_screenshots
from tests.helpers import XdgTestCase


class HistoryTest(XdgTestCase):
    def test_add_get_update_round_trip(self):
        h = History()
        r = h.add("/tmp/a.png", "full")
        self.assertEqual(r.status, "thinking")
        self.assertEqual(len(r.id), 32)
        h.update(r.id, answer="hi", status="done", attempts=1)
        h2 = History()
        h2.load()
        got = h2.get(r.id)
        self.assertEqual((got.answer, got.status, got.attempts, got.image, got.mode),
                         ("hi", "done", 1, "/tmp/a.png", "full"))

    def test_all_newest_first(self):
        h = History()
        ids = [h.add(f"/tmp/{i}.png", "full").id for i in range(3)]
        self.assertEqual([r.id for r in h.all()], ids[::-1])
        self.assertEqual(h.newest().id, ids[-1])

    def test_newest_empty_is_none(self):
        self.assertIsNone(History().newest())

    def test_load_marks_interrupted(self):
        h = History()
        a = h.add("/a.png", "full")
        b = h.add("/b.png", "full")
        h.update(b.id, status="retrying")
        c = h.add("/c.png", "full")
        h.update(c.id, status="done", answer="ok")
        h2 = History()
        h2.load()
        for rid in (a.id, b.id):
            self.assertEqual(h2.get(rid).status, "failed")
            self.assertEqual(h2.get(rid).error, INTERRUPTED)
        self.assertEqual(h2.get(c.id).status, "done")
        self.assertEqual(h2.get(c.id).error, "")

    def test_corrupt_history_is_empty(self):
        os.makedirs(os.path.dirname(config.history_path()), exist_ok=True)
        with open(config.history_path(), "w") as f:
            f.write("[{")
        h = History()
        h.load()
        self.assertEqual(h.all(), [])

    def test_entry_missing_fields_is_skipped(self):
        os.makedirs(os.path.dirname(config.history_path()), exist_ok=True)
        good = {"id": "a" * 32, "created": "2026-11-10T12:00:00", "image": "/x.png",
                "mode": "full", "status": "done", "answer": "x", "error": "", "attempts": 1}
        with open(config.history_path(), "w") as f:
            json.dump([{"id": "b" * 32}, good, "junk"], f)
        h = History()
        h.load()
        self.assertEqual([r.id for r in h.all()], ["a" * 32])

    def test_delete_removes_image(self):
        img = os.path.join(self.root, "x.png")
        open(img, "wb").close()
        h = History()
        r = h.add(img, "full")
        h.delete(r.id)
        self.assertFalse(os.path.exists(img))
        self.assertIsNone(h.get(r.id))

    def test_clear_keeps_images(self):
        img = os.path.join(self.root, "x.png")
        open(img, "wb").close()
        h = History()
        h.add(img, "full")
        h.clear()
        self.assertEqual(h.all(), [])
        self.assertTrue(os.path.exists(img))


class CleanupTest(XdgTestCase):
    def test_cleanup_only_own_old_files(self):
        now = time.time()
        old, new = now - 40 * 86400, now - 1 * 86400
        files = {"251001_120000.png": old, "251001_120000_2.png": old,
                 "260101_120000.png": new, "Screenshot.png": old, "notes.txt": old}
        shots = os.path.join(self.root, "shots")
        os.makedirs(shots)
        for name, mtime in files.items():
            p = os.path.join(shots, name)
            open(p, "wb").close()
            os.utime(p, (mtime, mtime))
        self.assertEqual(cleanup_screenshots(shots, 0, now), [])
        deleted = cleanup_screenshots(shots, 30, now)
        self.assertEqual(sorted(os.path.basename(p) for p in deleted),
                         ["251001_120000.png", "251001_120000_2.png"])
        self.assertEqual(sorted(os.listdir(shots)),
                         ["260101_120000.png", "Screenshot.png", "notes.txt"])

    def test_cleanup_missing_dir(self):
        self.assertEqual(cleanup_screenshots(os.path.join(self.root, "nope"), 30, time.time()), [])
