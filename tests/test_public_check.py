import importlib.util
import json
import pathlib
import sys
import unittest


MODULE_PATH = (
    pathlib.Path(__file__).parents[1] / "scripts" / "public_check.py"
)
spec = importlib.util.spec_from_file_location("public_check", MODULE_PATH)
public_check = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = public_check
assert spec.loader is not None
spec.loader.exec_module(public_check)


class PublicCheckTests(unittest.TestCase):
    def test_allowlist_is_exact_or_prefix_based(self):
        exact, prefixes = public_check.parse_allowlist(
            "README.md\nexamples/\n"
        )
        self.assertTrue(
            public_check.is_allowed_path("README.md", exact, prefixes)
        )
        self.assertTrue(
            public_check.is_allowed_path(
                "examples/demo.json",
                exact,
                prefixes,
            )
        )
        self.assertFalse(
            public_check.is_allowed_path("README.md.bak", exact, prefixes)
        )
        with self.assertRaises(ValueError):
            public_check.parse_allowlist("../private/\n")

    def test_examples_require_synthetic_provenance(self):
        synthetic = json.dumps(
            {"meta": {"provenance": "synthetic"}}
        ).encode()
        self.assertEqual(
            public_check.validate_text_content(
                "examples/demo.json",
                synthetic,
            ),
            [],
        )
        messages = public_check.validate_text_content(
            "examples/real.json",
            b"{}",
        )
        self.assertIn(
            "public example must declare meta.provenance=synthetic",
            messages,
        )

    def test_sensitive_content_is_reported_without_echoing_values(self):
        local_path = "/" + "Users" + "/alice/project"
        email = "alice" + "@personal.test"
        token = "gh" + "p_" + ("A" * 30)
        messages = public_check.validate_text_content(
            "README.md",
            f"{local_path}\n{email}\n{token}\n".encode(),
        )
        self.assertIn("contains a machine-local home path", messages)
        self.assertIn("contains a personal email address", messages)
        self.assertIn("contains a possible GitHub token", messages)
        self.assertNotIn(token, "\n".join(messages))

    def test_private_paths_and_unapproved_types_are_rejected(self):
        exact, prefixes = public_check.parse_allowlist(
            "README.md\nexamples/\n"
        )
        record = public_check.Record(
            ".private/session.csv",
            b"id,value\n1,2\n",
            "100644",
            "test",
        )
        messages = {
            issue.message
            for issue in public_check.validate_records(
                [record],
                exact,
                prefixes,
            )
        }
        self.assertIn("path is reserved for private/local data", messages)
        self.assertIn("path is outside .public-allowlist", messages)
        self.assertIn("file type is not approved for publication", messages)

    def test_symlink_must_stay_inside_repository(self):
        self.assertEqual(
            public_check.validate_symlink(
                ".skillshare/skills/demo/scripts",
                b"../../../scripts",
            ),
            [],
        )
        self.assertIn(
            "symlink target escapes the repository",
            public_check.validate_symlink("links/demo", b"../../private"),
        )


if __name__ == "__main__":
    unittest.main()
