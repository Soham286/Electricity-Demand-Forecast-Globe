import shutil
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")


def test_the_page_script_parses():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    js = HTML.split("<script>")[1].split("</script>")[0]
    done = subprocess.run([node, "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"], input=js, text=True, capture_output=True)
    assert done.returncode == 0, done.stderr


def test_every_file_the_page_loads_is_in_the_repo():
    for name in ("data.js", "world.js", "land.js"):
        assert f'src="{name}"' in HTML
    assert (WEB / "land.js").exists()
