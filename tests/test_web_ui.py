from __future__ import annotations

import re
import unittest
from pathlib import Path


WEB_INDEX = Path(__file__).parents[1] / "src" / "chfs" / "web" / "index.html"
WEB_APP = Path(__file__).parents[1] / "src" / "chfs" / "web" / "app.js"


class SharedTextToolbarTests(unittest.TestCase):
    """共享文本工具条的普通操作按钮应保持一致的有边框样式。"""

    def test_copy_button_uses_same_secondary_style_as_peer_actions(self) -> None:
        html = WEB_INDEX.read_text(encoding="utf-8")
        for button_id in ("copyTextButton", "pasteTextButton", "refreshTextButton"):
            match = re.search(
                rf'<button id="{button_id}" class="([^"]+)"',
                html,
            )
            self.assertIsNotNone(match, button_id)
            classes = set(match.group(1).split())
            self.assertIn("button", classes)
            self.assertIn("button-secondary", classes)
            self.assertNotIn("button-quiet", classes)

    def test_clear_shared_text_does_not_request_confirmation(self) -> None:
        javascript = WEB_APP.read_text(encoding="utf-8")
        match = re.search(
            r"function clearSharedText\(\) \{(?P<body>.*?)\n\}",
            javascript,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        self.assertNotIn("window.confirm", match.group("body"))

    def test_pasting_files_in_shared_text_uses_existing_upload_flow(self) -> None:
        """文件粘贴应阻止文本插入，并复用上传流程；纯文本粘贴仍交给浏览器。"""
        javascript = WEB_APP.read_text(encoding="utf-8")
        match = re.search(
            r"async function pasteFilesIntoSharedText\(event\) \{(?P<body>.*?)\n\}",
            javascript,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        body = match.group("body")
        self.assertIn("filesFromPasteEvent(event)", body)
        self.assertIn("if (!files.length) return", body)
        self.assertIn("event.preventDefault()", body)
        self.assertIn("await uploadFiles(files", body)
        self.assertIn(
            'elements.sharedText.addEventListener("paste", pasteFilesIntoSharedText)',
            javascript,
        )


if __name__ == "__main__":
    unittest.main()
