"""web/: static integrity checks for the vanilla frontend.

There is no bundler, type-checker or browser in the test run, so these catch
the failure modes that otherwise only show up as a dead button: an inline
`onclick="typo()"` with no such function, a nav item with no view behind it,
markup/CSS drifting apart, and a syntax error that blanks the whole app.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"
JS = (WEB / "app.js").read_text(encoding="utf-8")
CSS = (WEB / "styles.css").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")

# Not defined by us: event-handler expressions may legitimately call these.
BROWSER_GLOBALS = {"event", "confirm", "alert", "prompt", "setTimeout", "if", "return"}


def defined_names(src: str) -> set[str]:
    names = set(re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", src))
    names |= set(re.findall(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=", src))
    return names


def handler_calls(src: str) -> set[str]:
    """The first identifier called by each inline on*="..." attribute."""
    calls = set()
    for m in re.finditer(r'\bon(?:click|change|input|keydown|submit|focus|blur)="([^"]*)"', src):
        for stmt in re.split(r";", m.group(1)):
            g = re.match(r"\s*(?:return\s+)?([A-Za-z_$][\w$]*)\s*\(", stmt)
            if g:
                calls.add(g.group(1))
    return calls


class HandlerWiringTest(unittest.TestCase):
    def test_every_inline_handler_calls_a_function_that_exists(self):
        missing = sorted(c for c in handler_calls(JS + HTML)
                         if c not in defined_names(JS) and c not in BROWSER_GLOBALS)
        self.assertEqual(missing, [], f"inline handlers call undefined functions: {missing}")

    def test_handler_extractor_actually_finds_handlers(self):
        # guards the guard: if the regex silently matched nothing the test above
        # would pass forever
        calls = handler_calls(JS)
        for expected in ("go", "doPreview", "goProject", "setStepStatus", "draftSplit"):
            self.assertIn(expected, calls)

    def test_every_view_function_is_defined(self):
        block = re.search(r"const VIEWS = \{(.*?)\};", JS, re.S).group(1)
        for name in re.findall(r":\s*(view\w+)", block):
            self.assertIn(name, defined_names(JS))


class NavigationTest(unittest.TestCase):
    def test_every_nav_item_has_a_view(self):
        views = set(re.findall(r"(\w+):\s*view\w+", re.search(r"const VIEWS = \{(.*?)\};", JS, re.S).group(1)))
        routes = set(re.findall(r'data-route="(\w+)"', HTML))
        self.assertTrue(routes)
        self.assertLessEqual(routes, views)

    def test_accordion_targets_are_real_routes_or_the_prompt_focus_hook(self):
        views = set(re.findall(r"(\w+):\s*view\w+", re.search(r"const VIEWS = \{(.*?)\};", JS, re.S).group(1)))
        for target in re.findall(r"\bgo:\s*'(\w[\w-]*)'", JS):
            self.assertTrue(target in views or target == "focus-prompt", target)

    def test_accordion_art_keys_exist(self):
        art_keys = set(re.findall(r"^\s{2}(\w+):\s*`<svg", JS, re.M))
        used = set(re.findall(r"art:\s*'(\w+)'", JS))
        self.assertTrue(used)
        self.assertLessEqual(used, art_keys)


class MarkupCssTest(unittest.TestCase):
    def test_accordion_classes_used_by_js_are_styled(self):
        for cls in ("acc", "acc-item", "acc-art", "acc-body", "acc-title", "acc-cta", "acc-idx"):
            self.assertRegex(CSS, rf"\.{cls}\b", cls)
            self.assertIn(cls, JS, cls)

    def test_css_braces_are_balanced(self):
        stripped = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
        self.assertEqual(stripped.count("{"), stripped.count("}"))

    def test_dark_is_the_default_theme_before_first_paint(self):
        head = HTML.split("</head>")[0]
        self.assertIn("data-theme", head)
        self.assertIn("'dark'", head)
        self.assertLess(head.index("data-theme"), head.index("styles.css"))

    def test_page_is_pure_black_in_dark_and_light_theme_still_defined(self):
        self.assertRegex(CSS, r':root\[data-theme="dark"\]\s*\{[^}]*--page:\s*#000')
        self.assertIn('data-theme="light"', CSS)

    def test_no_hardcoded_hex_colours_in_the_new_accordion_art(self):
        art = JS[JS.index("const ACC_ART"):JS.index("function accordion")]
        self.assertNotRegex(art, r"#[0-9a-fA-F]{3,8}\b")

    def test_grid_cards_do_not_inherit_stacked_card_margin(self):
        self.assertRegex(CSS, r"\.grid\s*>\s*\.card\s*\+\s*\.card[^{]*\{\s*margin-top:\s*0")

    def test_accordion_css_uses_tokens_not_colour_literals(self):
        blk = CSS[CSS.index(".acc { display: flex;"):CSS.index("@media (max-width: 820px)", CSS.index(".acc { display: flex;"))]
        self.assertNotRegex(blk, r"#[0-9a-fA-F]{3,8}")
        self.assertNotRegex(blk, r"rgba?\(")

    def test_accordion_does_not_borrow_categorical_data_colours(self):
        blk = CSS[CSS.index(".acc { display: flex;"):CSS.index("@media (max-width: 820px)", CSS.index(".acc { display: flex;"))]
        self.assertNotIn("--series-", blk)

    def test_api_helper_recovers_from_a_stale_csrf_token(self):
        api = JS[JS.index("async function api("):JS.index("function toast(")]
        self.assertIn("CSRF", api)
        self.assertIn("_retried", api)          # retries once, never loops
        self.assertIn("still running", api)     # words, not "Failed to fetch"

    def test_navigation_resets_scroll_but_refresh_does_not(self):
        self.assertIn("lastViewKey", JS)
        self.assertRegex(JS, r"viewKey !== lastViewKey\)\s*\{\s*window\.scrollTo\(0, 0\)")

    def test_reduced_motion_is_still_honoured(self):
        self.assertIn("prefers-reduced-motion", CSS)


class SecurityConventionTest(unittest.TestCase):
    def test_accordion_interpolates_only_through_esc(self):
        body = JS[JS.index("function accordion"):JS.index("function accOpen")]
        for expr in re.findall(r"\$\{([^}]*)\}", body):
            ok = (expr.strip().startswith(("esc(", "ACC_ART[", "i ===", "i + 1", "items.map"))
                  or "i === 0" in expr)
            self.assertTrue(ok, f"unescaped interpolation in accordion(): ${{{expr}}}")


@unittest.skipUnless(shutil.which("node"), "node not installed — syntax check skipped")
class SyntaxTest(unittest.TestCase):
    def test_app_js_parses(self):
        r = subprocess.run(["node", "--check", str(WEB / "app.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
