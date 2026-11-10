import os
import tempfile
import unittest


class XdgTestCase(unittest.TestCase):
    """Points every XDG dir (and HOME) at a fresh temp dir for each test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name
        self._saved = {k: os.environ.get(k) for k in
                       ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")}
        os.environ["HOME"] = self.root
        for k, sub in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                       ("XDG_STATE_HOME", "state")):
            os.environ[k] = os.path.join(self.root, sub)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()
