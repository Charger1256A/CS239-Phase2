"""Private YAML section selection, shared CLI loading and safe parse failures."""

from pathlib import Path
from contextlib import redirect_stdout
import io
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

from policyweave.adapters.model_settings import load_connection
from policyweave.adapters.coding import CodingAgent
from policyweave.adapters.compatible import Connection
from tests.adapters.test_models import ModelFixture


class ConnectionFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "connection.yaml"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, value):
        self.path.write_text(yaml.safe_dump(value))

    def test_auto_selects_only_filled_key_and_defaults_to_openai_when_both_filled(self):
        for keys, provider in ((dict(openai="synthetic-openai"), "openai"),
                               (dict(anthropic="synthetic-anthropic"), "anthropic"),
                               (dict(openai="synthetic-openai", anthropic="synthetic-anthropic"), "openai")):
            with self.subTest(provider=provider, count=len(keys)):
                self.write({name: dict(apiKey=keys.get(name, "")) for name in ("openai", "anthropic")})
                settings = load_connection(self.path)
                self.assertEqual(settings["apiKey"], keys[provider])
                self.assertEqual(settings["protocol"], "messages" if provider == "anthropic" else "chat-completions")
                self.assertNotIn("openai", settings)
                self.assertNotIn("anthropic", settings)
                inactive = "anthropic" if provider == "openai" else "openai"
                if inactive in keys:
                    self.assertNotIn(keys[inactive], repr(settings))
                with patch("policyweave.adapters.coding.shutil.which", return_value="/synthetic/coder") as which:
                    self.assertEqual(CodingAgent(settings).messages, provider == "anthropic")
                    self.assertEqual(which.call_args.args, ("claude" if provider == "anthropic" else "codex",))

    def test_explicit_provider_overrides_priority_and_does_not_fall_back(self):
        value = dict(provider="anthropic", openai=dict(apiKey="synthetic-openai"),
                     anthropic=dict(apiKey="synthetic-anthropic"))
        self.write(value)
        self.assertEqual(load_connection(self.path)["apiKey"], "synthetic-anthropic")
        value["anthropic"]["apiKey"] = ""
        self.write(value)
        with self.assertRaisesRegex(ValueError, "Selected provider"):
            load_connection(self.path)

    def test_blank_optional_section_is_ignored_and_keyless_endpoint_requires_explicit_choice(self):
        self.write(dict(openai=None, anthropic=dict(apiKey="synthetic")))
        self.assertEqual(load_connection(self.path)["protocol"], "messages")
        self.write(dict(openai=dict(apiKey=None), anthropic=dict(apiKey="synthetic")))
        self.assertEqual(load_connection(self.path)["protocol"], "messages")
        self.write(dict(openai=dict(baseUrl="http://127.0.0.1/v1", modelId="local")))
        with self.assertRaisesRegex(ValueError, "Fill apiKey"):
            load_connection(self.path)
        self.write(dict(provider="openai", openai=dict(baseUrl="http://127.0.0.1/v1", modelId="local")))
        self.assertEqual(load_connection(self.path)["modelId"], "local")

    def test_connection_objects_use_same_selected_file_and_only_selected_key(self):
        with ModelFixture() as fixture:
            for name, protocol in (("anthropic", "messages"), ("openai", "chat-completions")):
                self.write(dict(provider=name, **{key: dict(apiKey="synthetic-" + key,
                    baseUrl=fixture.base, modelId="fixture") for key in ("openai", "anthropic")}))
                model = Connection(load_connection(self.path))
                self.assertEqual(model("test", "data"), '{"ok":true}')
                self.assertEqual(model.metadata["transport"], protocol)
                self.assertNotIn("synthetic-", repr(model.metadata))
                request = fixture.requests[-1]
                headers = {key.lower(): value for key, value in request["headers"].items()}
                credential = headers.get("x-api-key") or headers.get("authorization")
                self.assertIn("synthetic-" + name, credential)

    def test_rejects_json_files_flat_configs_and_unknown_sections(self):
        self.path = self.path.with_suffix(".json")
        self.path.write_text('{"apiKey":"synthetic-private"}')
        with self.assertRaisesRegex(ValueError, "yaml or .yml"):
            load_connection(self.path)
        self.path = self.path.with_suffix(".yaml")
        for value in (dict(apiKey="synthetic-private"), dict(antropic=dict(apiKey="synthetic-private")),
                      dict(provider="unknown"), dict(openai=False), dict(openai=dict(api_key="synthetic-private")),
                      dict(anthropic=dict(apiKey="synthetic", protocol="chat-completions"))):
            self.write(value)
            with self.assertRaises(ValueError) as error:
                load_connection(self.path)
            self.assertNotIn("synthetic-private", str(error.exception))

    def test_yaml_errors_duplicates_aliases_and_unsafe_tags_never_echo_secrets(self):
        for document in (
            'openai:\n  apiKey: "synthetic-private\n',
            'openai:\n  apiKey: synthetic-private\n  apiKey: another\n',
            'openai: !!python/object/apply:builtins.str [synthetic-private]\n',
            'openai: &a {apiKey: synthetic-private}\nanthropic: *a\n',
            'openai: {apiKey: synthetic-private}\n---\nother: value\n',
            'openai: {apiKey: "synthetic-private\\nextra"}\n',
        ):
            self.path.write_text(document)
            with self.assertRaises(ValueError) as error:
                load_connection(self.path)
            self.assertNotIn("synthetic-private", str(error.exception))

    def test_size_and_nesting_limits(self):
        self.path.write_bytes(b"x" * 65_537)
        with self.assertRaisesRegex(ValueError, "64 KiB"):
            load_connection(self.path)
        self.path.write_text("[" * 40 + "0" + "]" * 40)
        with self.assertRaises(ValueError):
            load_connection(self.path)

    def test_inspect_and_review_cli_load_same_yaml_without_a_coding_cli(self):
        from policyweave.workbench import analysis
        from policyweave.workbench import __main__ as review

        self.write(dict(anthropic=dict(apiKey="synthetic")))
        with (patch.object(sys, "argv", ["inspect", "--connection", str(self.path)]),
              patch.object(analysis, "AnalysisServer") as server, redirect_stdout(io.StringIO())):
            analysis.main()
            self.assertEqual(server.call_args.args[2].metadata["transport"], "messages")
        with (patch.object(sys, "argv", ["review", "--connection", str(self.path)]),
              patch.object(review, "Application"), patch.object(review, "Study"),
              patch.object(review, "Server") as server, redirect_stdout(io.StringIO())):
            review.main()
            self.assertEqual(server.call_args.args[2].metadata["transport"], "messages")


if __name__ == "__main__":
    unittest.main()
