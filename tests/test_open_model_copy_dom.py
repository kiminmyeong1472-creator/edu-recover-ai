"""Verify native mobile/browser selection with real DOM Range implementations."""
import json
import subprocess
import unittest
from pathlib import Path

from app.services.open_model_workspace import _HTML, chutes_workspace_html, learning_workspace_html


class OpenModelCopyDOMTests(unittest.TestCase):
    def test_native_copy_is_scoped_in_all_three_workspaces(self):
        for pathname, html in [
            ('/owner/open-models', _HTML),
            ('/owner/open-models/chutes', chutes_workspace_html()),
            ('/owner/open-models/learning', learning_workspace_html()),
        ]:
            with self.subTest(workspace=pathname):
                result = subprocess.run(
                    ['node', str(Path(__file__).with_name('open_model_copy_dom_harness.js'))],
                    input=json.dumps({'html': html, 'pathname': pathname}),
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
