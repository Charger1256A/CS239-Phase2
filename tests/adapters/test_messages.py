"""Messages wire payloads must not expose unsupported JSON Schema `pattern` constraints,
and schemas too large for Anthropic's output_config.format grammar compiler must fall
back to being described in the system prompt instead of being rejected outright."""

import json
import unittest

from policyweave.adapters.messages import MAX_WIRE_SCHEMA_BYTES, payload_for_messages, structured_schema
from policyweave.policy_adder.harness import planning_format


def _iter_schemas(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_schemas(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_schemas(value)


class StructuredSchemaPatternTests(unittest.TestCase):
    def test_pattern_constraint_moves_into_description(self):
        schema = dict(type="object", additionalProperties=False, properties=dict(
            name=dict(type="string", pattern="^[A-Za-z][A-Za-z_0-9]{0,63}$")))
        projected = structured_schema(schema)
        for node in _iter_schemas(projected):
            self.assertNotIn("pattern", node)
        self.assertIn("^[A-Za-z][A-Za-z_0-9]{0,63}$", projected["properties"]["name"]["description"])

    def test_large_schema_falls_back_to_system_text_instead_of_output_config_format(self):
        """Verified against the live Anthropic Messages API: the real binding-plan
        schema (PLAN_SHAPE, projected through structured_schema()) measures well
        over MAX_WIRE_SCHEMA_BYTES and is rejected with HTTP 400 "The compiled
        grammar is too large" when sent as output_config.format. It must instead
        be described in the system prompt so the request can succeed; the host
        validators (validate_plan et al.) are the real safety net either way."""
        response_format = planning_format("binding-plan")
        projected_size = len(json.dumps(structured_schema(response_format["json_schema"]["schema"])))
        self.assertGreater(projected_size, MAX_WIRE_SCHEMA_BYTES,
                            "test fixture assumption changed: binding-plan schema no longer exceeds "
                            "the wire size threshold, so the fallback path isn't exercised")

        payload = payload_for_messages(
            "claude-sonnet-5-5", 1024, "instructions", "prompt", "medium", response_format,
        )
        self.assertNotIn("format", payload.get("output_config", {}))
        self.assertEqual(payload["output_config"].get("effort"), "medium")
        self.assertIn("^[A-Za-z][A-Za-z_0-9]{0,63}$", payload["system"])
        self.assertIn('"calls"', payload["system"])
        self.assertNotIn('"pattern"', payload["system"])

    def test_small_schema_still_uses_output_config_format(self):
        response_format = planning_format("inventory")
        payload = payload_for_messages(
            "claude-sonnet-5-5", 1024, "instructions", "prompt", None, response_format,
        )
        self.assertIn("schema", payload["output_config"]["format"])


if __name__ == "__main__":
    unittest.main()
