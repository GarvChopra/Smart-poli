"""sw-push.js runs in the phone, outside any page, so pytest cannot import it: tests/sw_push.test.js runs it in a stand-in
service-worker world (Node) and checks the notification buttons and what pressing them does."""
import os
import shutil
import subprocess

import pytest

NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_notification_buttons_and_clicks_behave():
    here = os.path.dirname(os.path.abspath(__file__))
    out = subprocess.run([NODE, os.path.join(here, "sw_push.test.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "all checks passed" in out.stdout
