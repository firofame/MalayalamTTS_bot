import argparse
import json
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch
import zipfile

import httpx

import deploy


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        output = patch("builtins.print")
        output.start()
        self.addCleanup(output.stop)

    def args(self, switch=False):
        return argparse.Namespace(
            resource_group="test-group", speech_resource="test-speech",
            location="centralindia", switch_webhook=switch,
        )

    def test_cli_errors_do_not_expose_secrets(self):
        failed = subprocess.CompletedProcess(["az"], 1, stdout="secret", stderr="secret")
        with patch.object(deploy.subprocess, "run", return_value=failed):
            with self.assertRaises(RuntimeError) as error:
                deploy.run_az("deployment", "group", "create")
        self.assertNotIn("secret", str(error.exception))

    def test_login_is_required_before_provisioning(self):
        with (
            patch.object(deploy.shutil, "which", return_value="/usr/bin/az"),
            patch.object(deploy, "run_az", side_effect=RuntimeError("Not signed in")) as az,
        ):
            with self.assertRaisesRegex(RuntimeError, "Run az login"):
                deploy.deploy(self.args())
        self.assertEqual(az.call_count, 1)

    def test_health_failure_prevents_cutover(self):
        client = Mock(get=Mock(return_value=httpx.Response(503)))
        with patch.object(deploy.time, "sleep"):
            with self.assertRaisesRegex(RuntimeError, "webhook has not been changed"):
                deploy.wait_for_health(client, "https://example.test")
        self.assertEqual(client.get.call_count, 18)

    def test_success_response_without_expected_health_status_is_not_ready(self):
        client = Mock(get=Mock(return_value=httpx.Response(200, json={"status": "not_configured"})))
        with patch.object(deploy.time, "sleep"):
            with self.assertRaises(RuntimeError):
                deploy.wait_for_health(client, "https://example.test")

    def test_token_is_not_exposed_in_telegram_connection_failure(self):
        client = Mock(post=Mock(side_effect=httpx.ConnectError("https://telegram.test/botsecret")))
        with self.assertRaises(RuntimeError) as error:
            deploy.telegram_request(client, "secret", "getMe", {})
        self.assertNotIn("secret", str(error.exception))

    def test_provision_packaging_and_optional_cutover(self):
        for switch in (False, True):
            with self.subTest(switch=switch):
                calls = []

                def az(*arguments):
                    if arguments[:3] == ("deployment", "group", "create"):
                        path = Path(arguments[arguments.index("--parameters") + 1][1:])
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                        params = json.loads(path.read_text())
                        self.assertEqual(params["botToken"]["value"], "test-token")
                        return json.dumps({
                            "functionAppName": {"value": "malayalamtts-test"},
                            "baseUrl": {"value": "https://azure.test"},
                        })
                    if arguments[:4] == ("functionapp", "deployment", "source", "config-zip"):
                        path = Path(arguments[arguments.index("--src") + 1])
                        with zipfile.ZipFile(path) as archive:
                            self.assertEqual(set(archive.namelist()), set(deploy.RUNTIME_FILES))
                            self.assertNotIn("local.settings.json", archive.namelist())
                        return ""
                    return ""

                def telegram(client, token, method, payload):
                    calls.append((method, payload))
                    return {"ok": True, "result": {
                        "url": "https://azure.test/api/telegram" if method == "getWebhookInfo" and len(calls) > 2
                        else "https://render.test/telegram",
                    }}

                client = Mock()
                client.__enter__ = Mock(return_value=client)
                client.__exit__ = Mock()
                client.post.return_value = httpx.Response(401)
                with (
                    patch.dict(os.environ, {"BOT_TOKEN": "test-token", "TELEGRAM_WEBHOOK_SECRET": "test-secret"}),
                    patch.object(deploy.shutil, "which", return_value="/usr/bin/az"),
                    patch.object(deploy, "run_az", side_effect=az),
                    patch.object(deploy, "telegram_request", side_effect=telegram),
                    patch.object(deploy, "wait_for_health"),
                    patch.object(deploy.httpx, "Client", return_value=client),
                ):
                    deploy.deploy(self.args(switch))
                webhook_calls = [payload for method, payload in calls if method == "setWebhook"]
                self.assertEqual(len(webhook_calls), int(switch))
                if switch:
                    self.assertEqual(webhook_calls[0], {
                        "url": "https://azure.test/api/telegram", "secret_token": "test-secret",
                        "allowed_updates": ["message"], "drop_pending_updates": False,
                    })
