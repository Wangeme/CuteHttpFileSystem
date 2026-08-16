from __future__ import annotations

import re
import unittest
from pathlib import Path


WEB_INDEX = Path(__file__).parents[1] / "src" / "chfs" / "web" / "index.html"


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


if __name__ == "__main__":
    unittest.main()
