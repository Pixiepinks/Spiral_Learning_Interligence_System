import ast
import json
import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from flask import Flask, Response, jsonify, request


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def load_app_nodes(*names, include_true_values=False):
    """Load isolated app.py functions without running its database migrations."""
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
    selected = []
    for node in tree.body:
        if include_true_values and isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == "WHATSAPP_ENABLED_TRUE_VALUES" for target in node.targets):
                selected.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            selected.append(node)
    namespace = {"os": os}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(APP_PATH), "exec"), namespace)
    return namespace


class WhatsAppFeatureFlagTests(unittest.TestCase):
    def setUp(self):
        loaded = load_app_nodes("is_whatsapp_enabled", include_true_values=True)
        self.is_enabled = loaded["is_whatsapp_enabled"]

    def test_missing_and_false_values_are_disabled(self):
        for value in (None, "", "false", "0", "no", "off", "anything"):
            environment = {} if value is None else {"WHATSAPP_ENABLED": value}
            with self.subTest(value=value), patch.dict(os.environ, environment, clear=True):
                self.assertFalse(self.is_enabled())

    def test_common_true_values_are_enabled_case_insensitively(self):
        for value in ("true", "1", "yes", "on", " TRUE ", "Yes", "ON"):
            with self.subTest(value=value), patch.dict(os.environ, {"WHATSAPP_ENABLED": value}, clear=True):
                self.assertTrue(self.is_enabled())

    def test_disabled_webhook_acknowledges_without_processing(self):
        flask_app = Flask(__name__)
        save_messages = Mock()
        queue_reply = Mock()
        namespace = {
            "app": flask_app,
            "os": os,
            "request": request,
            "Response": Response,
            "jsonify": jsonify,
            "is_whatsapp_enabled": lambda: False,
            "count_incoming_whatsapp_messages": Mock(),
            "save_incoming_whatsapp_messages": save_messages,
            "queue_whatsapp_auto_reply": queue_reply,
        }
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
        webhook_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "whatsapp_webhook")
        exec(compile(ast.Module(body=[webhook_node], type_ignores=[]), str(APP_PATH), "exec"), namespace)

        client = flask_app.test_client()
        response = client.post("/webhook/whatsapp", json={"entry": [{"changes": []}]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "processed": False})
        save_messages.assert_not_called()
        queue_reply.assert_not_called()

    def test_get_verification_is_preserved_while_disabled(self):
        flask_app = Flask(__name__)
        namespace = {
            "app": flask_app,
            "os": os,
            "request": request,
            "Response": Response,
            "jsonify": jsonify,
            "is_whatsapp_enabled": lambda: False,
        }
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
        webhook_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "whatsapp_webhook")
        exec(compile(ast.Module(body=[webhook_node], type_ignores=[]), str(APP_PATH), "exec"), namespace)

        with patch.dict(os.environ, {"WHATSAPP_VERIFY_TOKEN": "verify-me"}, clear=True):
            response = flask_app.test_client().get(
                "/webhook/whatsapp?hub.mode=subscribe&hub.verify_token=verify-me&hub.challenge=challenge-123"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "challenge-123")

    def test_enabled_webhook_keeps_existing_processing_and_reply_flow(self):
        flask_app = Flask(__name__)
        saved_message = type("SavedMessage", (), {"from_number": "94700000000", "id": 42})()
        save_messages = Mock(return_value=[saved_message])
        queue_reply = Mock()
        namespace = {
            "app": flask_app,
            "os": os,
            "json": json,
            "request": request,
            "Response": Response,
            "jsonify": jsonify,
            "is_whatsapp_enabled": lambda: True,
            "count_incoming_whatsapp_messages": Mock(return_value=1),
            "save_incoming_whatsapp_messages": save_messages,
            "queue_whatsapp_auto_reply": queue_reply,
        }
        tree = ast.parse(APP_PATH.read_text(encoding="utf-8"), filename=str(APP_PATH))
        webhook_node = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "whatsapp_webhook")
        exec(compile(ast.Module(body=[webhook_node], type_ignores=[]), str(APP_PATH), "exec"), namespace)

        response = flask_app.test_client().post("/webhook/whatsapp", json={"entry": [{"changes": []}]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"success": True, "saved": 1})
        save_messages.assert_called_once()
        queue_reply.assert_called_once_with("94700000000", 42)


if __name__ == "__main__":
    unittest.main()
