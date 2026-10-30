"""Provision and publish the Azure bot; change Telegram's webhook only when requested."""
import argparse
import getpass
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import time
import zipfile

import httpx

ROOT = Path(__file__).resolve().parent
RUNTIME_FILES = (
    "function_app.py", "main.py", "storage.py", "telegram_client.py", "tts.py",
    "host.json", "requirements.txt",
)
logging.getLogger("httpx").setLevel(logging.WARNING)


def run_az(*arguments: str) -> str:
    result = subprocess.run(
        ["az", *arguments, "--only-show-errors"], capture_output=True, text=True, check=False,
    )
    if result.returncode:
        # CLI failures can include settings or secure deployment parameters.
        raise RuntimeError(f"Azure command failed: az {' '.join(arguments[:3])}. "
                           "Check Azure login, permissions, region availability, and deployment status.")
    return result.stdout


def telegram_request(client: httpx.Client, token: str, method: str, payload: dict) -> dict:
    try:
        response = client.post(f"https://api.telegram.org/bot{token}/{method}", json=payload)
    except httpx.HTTPError:
        raise RuntimeError(f"Telegram {method} could not be reached.") from None
    if response.status_code != 200:
        raise RuntimeError(f"Telegram {method} failed (HTTP {response.status_code}).")
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError(f"Telegram {method} returned invalid JSON.") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise RuntimeError(f"Telegram {method} rejected the request.")
    return result


def wait_for_health(client: httpx.Client, base_url: str) -> None:
    for attempt in range(18):
        try:
            response = client.get(f"{base_url}/api/health")
        except httpx.HTTPError:
            response = None
        if response is not None and response.status_code == 200:
            try:
                healthy = response.json().get("status") == "running"
            except (ValueError, AttributeError):
                healthy = False
            if healthy:
                return
        print(f"Waiting for Azure health check ({attempt + 1}/18)...", flush=True)
        time.sleep(10)
    raise RuntimeError("Azure health check failed. Telegram's webhook has not been changed.")


def deploy(args: argparse.Namespace) -> None:
    if not shutil.which("az"):
        raise RuntimeError("Install Azure CLI, then run az login.")
    try:
        run_az("account", "show", "--query", "state", "--output", "tsv")
    except RuntimeError:
        raise RuntimeError("Azure CLI is not authenticated. Run az login, then retry.") from None
    local_settings = ROOT / "local.settings.json"
    values = json.loads(local_settings.read_text()).get("Values", {}) if local_settings.exists() else {}
    token = os.environ.get("BOT_TOKEN") or values.get("BOT_TOKEN") or getpass.getpass("Telegram bot token: ")
    if not token:
        raise RuntimeError("BOT_TOKEN is required.")
    with httpx.Client(timeout=30.0) as client:
        telegram_request(client, token, "getMe", {})
        current = telegram_request(client, token, "getWebhookInfo", {})["result"]
        secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET") or values.get("TELEGRAM_WEBHOOK_SECRET")
        if not secret:
            # Keep the existing secret when re-deploying, so traffic is not interrupted.
            apps = json.loads(run_az(
                "functionapp", "list", "--resource-group", args.resource_group,
                "--query", "[?starts_with(name, 'malayalamtts-')].name", "--output", "json",
            ))
            if len(apps) > 1:
                raise RuntimeError("Multiple bot Function Apps found. Set TELEGRAM_WEBHOOK_SECRET explicitly.")
            if apps:
                settings = json.loads(run_az(
                    "functionapp", "config", "appsettings", "list",
                    "--resource-group", args.resource_group, "--name", apps[0],
                    "--query", "[?name=='TELEGRAM_WEBHOOK_SECRET'].value", "--output", "json",
                ))
                secret = settings[0] if settings else None
        secret = secret or secrets.token_urlsafe(32)
        if not 1 <= len(secret) <= 256 or any(
            char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in secret
        ):
            raise RuntimeError("TELEGRAM_WEBHOOK_SECRET must be 1-256 letters, digits, underscores or hyphens.")
        print("Provisioning a separate Python Flex Consumption app...", flush=True)
        with tempfile.TemporaryDirectory(prefix="malayalamtts-deploy-") as directory:
            parameters = Path(directory) / "parameters.json"
            parameters.touch(mode=0o600)
            parameters.write_text(json.dumps({
                "location": {"value": args.location},
                "speechResourceName": {"value": args.speech_resource},
                "botToken": {"value": token},
                "webhookSecret": {"value": secret},
            }))
            outputs = json.loads(run_az(
                "deployment", "group", "create", "--resource-group", args.resource_group,
                "--name", "malayalamtts", "--template-file", str(ROOT / "infra" / "main.bicep"),
                "--parameters", f"@{parameters}", "--query", "properties.outputs", "--output", "json",
            ))
            app_name = outputs["functionAppName"]["value"]
            base_url = outputs["baseUrl"]["value"]
            archive = Path(directory) / "bot.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
                for name in RUNTIME_FILES:
                    package.write(ROOT / name, name)
            print(f"Publishing {app_name} with a remote Python build...", flush=True)
            run_az(
                "functionapp", "deployment", "source", "config-zip",
                "--resource-group", args.resource_group, "--name", app_name,
                "--src", str(archive), "--build-remote", "true", "--timeout", "600", "--output", "none",
            )
        wait_for_health(client, base_url)
        rejected = client.post(f"{base_url}/api/telegram", json={})
        if rejected.status_code != 401:
            raise RuntimeError("Azure webhook authentication check failed. Webhook was not changed.")
        webhook = f"{base_url}/api/telegram"
        if args.switch_webhook:
            telegram_request(client, token, "setMyCommands", {"commands": [
                {"command": "start", "description": "Start the bot"},
                {"command": "tts", "description": "Convert text to speech"},
            ]})
            telegram_request(client, token, "setWebhook", {
                "url": webhook,
                "secret_token": secret,
                "allowed_updates": ["message"],
                "drop_pending_updates": False,
            })
            info = telegram_request(client, token, "getWebhookInfo", {})["result"]
            if info.get("url") != webhook:
                raise RuntimeError("Telegram did not confirm the Azure webhook. Inspect getWebhookInfo.")
            print("Telegram now points to Azure. Test /start and /tts in Telegram before removing the previous host.")
        else:
            print("Azure is ready. Telegram's webhook is unchanged.")
            print("Re-run with --switch-webhook to cut over after checking the deployment.")
        if current.get("url") and current["url"] != webhook:
            print("The previous host has not been stopped or deleted.")
        print(f"Azure health: {base_url}/api/health")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resource-group", default="rg-read-aloud-0b33fdb4")
    parser.add_argument("--speech-resource", default="read-aloud-speech-0b33fdb4")
    parser.add_argument("--location", default="centralindia")
    parser.add_argument("--switch-webhook", action="store_true")
    args = parser.parse_args()
    try:
        deploy(args)
    except (RuntimeError, ValueError, OSError, httpx.HTTPError) as error:
        # Never print HTTPX exceptions, which may contain a Telegram token.
        message = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        parser.exit(1, f"Deployment failed: {message}\n")


if __name__ == "__main__":
    main()
