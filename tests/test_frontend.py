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

    def test_both_themes_are_defined(self):
        self.assertIn(':root[data-theme="dark"]', CSS)
        self.assertIn(':root[data-theme="light"]', CSS)

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


class NotClaudeDedicatedTest(unittest.TestCase):
    def test_an_unknown_model_is_not_assumed_to_be_claude(self):
        fn = JS[JS.index("function isClaudeModel"):JS.index("async function commitPlan")]
        self.assertIn("!!m &&", fn)                  # unknown -> false
        self.assertIn("!S.models.length", fn)        # only "true" before the catalogue loads

    def test_nav_names_the_screen_by_what_it_shows_not_by_one_vendor(self):
        self.assertRegex(HTML, re.compile(r'data-route="dashboard">.*?Usage</button>', re.S))
        self.assertNotIn("Windows</button>", HTML)

    def test_usage_screen_lists_every_agent_and_offers_plan_windows_as_opt_in(self):
        self.assertIn("function usageByModelCard", JS)
        self.assertIn("function planWindowOptIn", JS)
        gate = JS[JS.index("function renderWindowsGate"):JS.index("async function viewDashboard")]
        self.assertIn("usageByModelCard(u)", gate)

    def test_setup_lets_any_agent_connect_and_any_model_be_added(self):
        for needle in ("Connect any agent", "Your own models", "--log-usage", "saveCustomModel", "removeCustomModel"):
            self.assertIn(needle, JS, needle)

    def test_no_screen_copy_says_a_prompt_is_for_claude(self):
        self.assertNotIn("send to Claude", JS)
        self.assertNotIn("paste it into Claude", JS)

    def test_split_savings_text_only_mentions_a_window_for_claude_models(self):
        i = JS.index("Do it anyway only for the reasons that are not about price")
        seg = JS[i:i + 400]
        self.assertIn("isClaudeModel(e.model)", seg)

    def test_usage_page_leads_with_all_agents_and_claude_limits_come_after(self):
        body = JS[JS.index("async function viewDashboard"):JS.index("async function seedDemo")]
        table = body.index("usageByModelCard(u)")
        limits_section = body.index("subscriptionIntro()")
        self.assertLess(table, limits_section)               # every agent first, plan limits second
        head = body[body.index("<div class=\"page-head\">"):table]
        self.assertNotIn("Sync from Claude", head)           # no Claude action in the page header
        self.assertNotIn("Claude", head.replace("Claude Code", ""))

    def test_subscription_limits_cover_codex_and_gemini_not_only_claude(self):
        for fn in ("function providerLimitCard", "function providerLimitCards", "async function saveLimits",
                   "function subscriptionIntro"):
            self.assertIn(fn, JS, fn)
        # both the Claude-enabled page and the everyone-else page show the same provider cards
        dash = JS[JS.index("async function viewDashboard"):JS.index("async function seedDemo")]
        gate = JS[JS.index("function renderWindowsGate"):JS.index("async function viewDashboard")]
        self.assertIn("providerLimitCards(lim)", dash)
        self.assertIn("providerLimitCards(lim)", gate)
        self.assertIn("subscriptionIntro()", dash)
        self.assertIn("subscriptionIntro()", gate)
        self.assertIn("/api/limits", dash)

    def test_claude_is_one_plan_among_several_in_the_section(self):
        self.assertIn("Claude (Pro / Max)", JS)
        self.assertNotIn("Claude subscription limits", JS)   # the section is "Subscription limits"
        self.assertIn("Hide Claude limits", JS)              # hiding Claude does not hide Codex/Gemini

    def test_an_unset_limit_renders_as_unknown_never_as_an_empty_bar(self):
        card = JS[JS.index("function providerLimitCard("):JS.index("function providerLimitCards")]
        self.assertIn("meter-track unknown", card)
        self.assertIn("no limit set", card)

    def test_limit_inputs_are_escaped_and_labelled(self):
        card = JS[JS.index("function providerLimitCard("):JS.index("function providerLimitCards")]
        self.assertIn('aria-label="${esc(p.label)}', card)
        self.assertNotRegex(card, r"value=\"\$\{w\.limit\}")   # never a raw interpolation into an attribute

    def test_claude_specific_sections_are_labelled_as_claude_code(self):
        self.assertIn("Claude Code live status line", JS)
        self.assertIn("Claude Code session tracking", JS)


AW = CSS[CSS.index("Awwards pass (September 2026, part 7)"):]


def _block(selector: str) -> dict:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", AW, re.S)
    assert m, selector
    return dict(re.findall(r"(--[\w-]+):\s*([^;]+);", m.group(1)))


BASE_T, DARK_T, LIGHT_T = _block(":root"), _block(':root[data-theme="dark"]'), _block(':root[data-theme="light"]')


def _resolve(val, theme):
    """A token's value as an (r, g, b) tuple, following var() aliases and solid
    color-mix(); None for anything with transparency (hairlines), which no text sits on."""
    val = val.strip()
    m = re.fullmatch(r"var\((--[\w-]+)\)", val)
    if m:
        return _resolve(theme.get(m.group(1)) or BASE_T[m.group(1)], theme)
    if re.fullmatch(r"#[0-9A-Fa-f]{6}", val):
        return tuple(int(val[i:i + 2], 16) for i in (1, 3, 5))
    m = re.fullmatch(r"color-mix\(in srgb,\s*(.+?)\s+(\d+)%,\s*(.+)\)", val)
    if m and m.group(3).strip() != "transparent":
        a, b = _resolve(m.group(1), theme), _resolve(m.group(3), theme)
        if a and b:
            p = int(m.group(2)) / 100
            return tuple(round(a[i] * p + b[i] * (1 - p)) for i in range(3))
    return None


def _contrast(a, b) -> float:
    def lum(rgb):
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, bl = (f(c / 255) for c in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * bl
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


class AwwardsDesignSystemTest(unittest.TestCase):
    """The UI follows the user's "Awwards" design system: six brand colours, one
    focus accent, Archivo, flat cards, pill buttons. These pin the parts that can
    silently rot — the exact palette, accessibility, and the colour rules."""

    BRAND = {"--beige": "#D9CDAD", "--brown-dark": "#594D40", "--dark-green": "#2F2E25",
             "--green-dark": "#465016", "--green-darker": "#23280B", "--light": "#DFDDCB",
             "--accent-focus": "#3D5AFE"}

    def test_the_palette_is_exactly_the_design_systems(self):
        for name, hexv in self.BRAND.items():
            self.assertEqual(BASE_T[name].upper(), hexv, name)

    def test_text_contrast_is_at_least_4_5_to_1_in_both_themes(self):
        pairs = [("text-primary", "page"), ("text-primary", "glass"), ("text-primary", "inset"),
                 ("text-muted", "page"), ("text-muted", "glass"), ("text-muted", "inset"),
                 ("cta-ink", "cta-bg"), ("accent-ink", "accent"), ("nav-ink", "nav-bg"),
                 ("success-text", "glass"), ("critical", "glass"), ("critical", "inset"),
                 ("warning", "glass"), ("serious", "glass"),
                 ("text-primary", "warning-bg"), ("text-primary", "serious-bg")]
        for theme_name, theme in (("dark", DARK_T), ("light", LIGHT_T)):
            for fg, bg in pairs:
                a = _resolve(theme.get("--" + fg) or BASE_T["--" + fg], theme)
                b = _resolve(theme.get("--" + bg) or BASE_T["--" + bg], theme)
                self.assertTrue(a and b, f"{theme_name}: cannot resolve {fg} on {bg}")
                self.assertGreaterEqual(_contrast(a, b), 4.5, f"{theme_name}: {fg} on {bg}")

    def test_contrast_helper_is_right(self):
        self.assertAlmostEqual(_contrast((0, 0, 0), (255, 255, 255)), 21.0, places=1)
        self.assertAlmostEqual(_contrast((0xDF, 0xDD, 0xCB), (0x2F, 0x2E, 0x25)), 9.97, places=1)   # the system's own figure

    def test_brand_colours_are_declared_once_and_only_tokens_are_used_after(self):
        body = re.sub(r"/\*.*?\*/", "", AW, flags=re.S)
        # drop the three token blocks, where hexes belong
        body = re.sub(r":root(?:\[data-theme=\"\w+\"\])?\s*\{.*?\n\}", "", body, flags=re.S)
        self.assertNotRegex(body, r"#[0-9A-Fa-f]{3,8}\b")
        self.assertNotRegex(body, r"rgba?\(")

    def test_electric_blue_is_only_for_focus_and_the_one_highlight(self):
        body = re.sub(r"/\*.*?\*/", "", AW, flags=re.S)
        body = re.sub(r":root(?:\[data-theme=\"\w+\"\])?\s*\{.*?\n\}", "", body, flags=re.S)
        for rule in re.findall(r"([^{}]+)\{([^{}]*--accent-focus[^{}]*)\}", body):
            selector = rule[0]
            self.assertTrue(":focus" in selector or ".mark" in selector, selector.strip())

    def test_cards_are_flat_and_buttons_are_pills(self):
        self.assertEqual(BASE_T["--r"].strip(), "0px")
        self.assertRegex(AW, r"button, \.btn \{[^}]*border-radius: 9999px")
        self.assertRegex(AW, r"\.card, \.proj, \.step, \.modal \{[^}]*border-radius: 0")
        self.assertRegex(AW, r"--card-drop: 4px 4px 0 var\(--green-darker\)")   # hard offset, never blurred

    def test_banners_are_tinted_panels_not_a_coloured_left_edge(self):
        self.assertRegex(AW, r"\.banner \{[^}]*border-left-width: 1px")

    def test_archivo_is_bundled_locally_and_nothing_is_fetched_from_the_network(self):
        self.assertTrue((WEB / "fonts" / "archivo-latin.woff2").is_file())
        self.assertEqual((WEB / "fonts" / "archivo-latin.woff2").read_bytes()[:4], b"wOF2")
        self.assertIn('url("/fonts/archivo-latin.woff2")', CSS)
        for name, src in (("styles.css", CSS), ("index.html", HTML), ("app.js", JS)):
            self.assertNotIn("fonts.googleapis", src, name)
            self.assertNotIn("fonts.gstatic", src, name)
            self.assertNotRegex(src, r"""(?:src|href)=["']https?://""", name)

    def test_both_widths_come_from_the_one_variable_font(self):
        self.assertRegex(AW, r"font-weight: 100 900;\s*font-stretch: 62% 125%")
        self.assertIn("font-stretch: 125%", AW)                  # the Extended cut, for display + CTAs

    def test_motion_uses_the_systems_easing_and_respects_reduced_motion(self):
        for curve in ("cubic-bezier(0.05, 0.7, 0.1, 1)", "cubic-bezier(0.55, 0, 1, 0.45)", "cubic-bezier(0.34, 1.4, 0.64, 1)"):
            self.assertIn(curve, AW)
        self.assertRegex(AW, r"prefers-reduced-motion: reduce\) \{[^}]*hover[^}]*transform: none")

    def test_the_plan_headline_carries_the_single_highlight_marker(self):
        self.assertEqual(JS.count('class="mark"'), 1)
        self.assertIn('class="hero-block"', JS)

    def test_dividers_are_scalloped_not_plain_rules(self):
        self.assertIn("radial-gradient(circle at 12px 0", AW)
        self.assertGreaterEqual(JS.count('<div class="scallop"></div>'), 2)


@unittest.skipUnless(shutil.which("node"), "node not installed — syntax check skipped")
class SyntaxTest(unittest.TestCase):
    def test_app_js_parses(self):
        r = subprocess.run(["node", "--check", str(WEB / "app.js")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
