import unittest

import app
import layout
import page_camps_hub


class CampsHubNavigationTest(unittest.TestCase):
    def test_sidebar_has_expected_four_groups(self):
        self.assertEqual(
            [name for name, _items in layout.MENU_GROUPS],
            ["基本設定", "生徒情報", "講師情報", "講習会"],
        )

    def test_sidebar_only_links_to_camps_hub_for_camp_group(self):
        camp_items = next(items for name, items in layout.MENU_GROUPS if name == "講習会")
        self.assertEqual(camp_items, [("/camps-hub", "講習会")])

        rendered = layout.render_page("/camps-hub", "").decode("utf-8")
        nav_html = rendered.split("<nav>", 1)[1].split("</nav>", 1)[0]
        self.assertEqual(nav_html.count('href="/camps-hub"'), 1)
        self.assertNotIn('href="/camps"', nav_html)
        self.assertNotIn('href="/run-scheduler"', nav_html)

    def test_person_schedule_links_are_in_person_groups(self):
        groups = {name: dict(items) for name, items in layout.MENU_GROUPS}
        self.assertEqual(groups["生徒情報"]["/schedule-student"], "生徒視点の時間割")
        self.assertEqual(groups["講師情報"]["/schedule-instructor"], "講師視点の時間割")

    def test_hub_has_four_cards_and_all_camp_routes(self):
        html = page_camps_hub.render({})
        self.assertEqual(html.count('class="hub-card"'), 4)
        expected_paths = {
            "/camps",
            "/camp-enrollments",
            "/camp-sync-groups",
            "/camp-availability-student",
            "/image-import",
            "/image-import-review",
            "/image-import-corrections",
            "/camp-availability-instructor",
            "/run-scheduler",
            "/schedule-by-day",
        }
        for path in expected_paths:
            with self.subTest(path=path):
                self.assertIn(f'href="{path}"', html)
                self.assertIn(path, app.ROUTES)

        self.assertLess(html.index('href="/image-import"'), html.index('href="/image-import-review"'))
        self.assertLess(
            html.index('href="/image-import-review"'),
            html.index('href="/image-import-corrections"'),
        )

    def test_hub_route_is_get_only(self):
        self.assertEqual(app.ROUTES["/camps-hub"], (page_camps_hub.render, None))


if __name__ == "__main__":
    unittest.main()
