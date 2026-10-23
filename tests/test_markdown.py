"""The window's Markdown renderer (JavaScript), checked under node when it's installed."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="node isn't installed")
def test_markdown_renderer_is_safe_and_fast():
    r = subprocess.run(["node", str(Path(__file__).with_name("md_check.js"))], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
