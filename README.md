# MalayalamTTS_bot

Telegram-only bot that converts text directly to speech without translation.

**Bot:** [t.me/MalayalamTTS_bot](https://t.me/MalayalamTTS_bot)

## Usage

Send any of these to the bot:

- **Plain text** — converts directly to speech
- `/tts <text>` — convert text to speech

Link extraction and downloading are not supported. Links are treated as plain
text, not fetched. Text input is limited to 4,000 characters, with a
30-second cooldown between speech requests per chat.

Local file input is not supported. File paths in messages are treated as plain text.

### How it works

| Input | Process |
|-------|---------|
| Plain text | Reads the supplied text aloud |
| `/tts <text>` | Reads the supplied text aloud |

The bot sends the original text as a plain-text caption on the voice message,
truncated to Telegram's 1,024-character caption limit.

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Start the bot |
| `/tts` | Convert text to speech |

## Deploy

Render is no longer required. The bot uses **Python 3.12 Azure Functions Flex
Consumption**, Azure Storage Queues and Tables, and the official Azure Speech
REST API. It does not use Edge TTS or require an always-running web server.

```text
Telegram -> authenticated HTTP Function -> Storage Queue -> Python worker
                                                            |
                                                       Azure Speech
                                                            |
                                                         Telegram
```

The deployment uses a **separate** Python Function App and storage account.
It reuses the existing Speech resource from the sibling TTS project without
changing that project's Node.js Function App or Speech pricing tier:

- Resource group: `rg-read-aloud-0b33fdb4`
- Speech resource: `read-aloud-speech-0b33fdb4`
- Default region: `centralindia`

Current bot deployment:

| Resource | Name |
|----------|------|
| Python Function App | `malayalamtts-uppsfe24x3kue` |
| Dedicated storage | `mlttsuppsfe24x3kue` |
| Health endpoint | <https://malayalamtts-uppsfe24x3kue.azurewebsites.net/api/health> |
| Telegram webhook | <https://malayalamtts-uppsfe24x3kue.azurewebsites.net/api/telegram> |

**Migration status:** the webhook was cut over to Azure on 2026-10-30, and a
live `/tts` request produced a playable voice message. The old Render service is
no longer used; delete it from the Render dashboard and remove any leftover
environment variables or deploy hooks there.

### Provision and publish

Install Python 3.12+ and [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli).
The selected subscription must contain the resource group and Speech resource,
and you need permission to deploy Function Apps and storage and read Speech keys.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
az login
az bicep install
python deploy.py
```

The deployment script securely prompts for the Telegram bot token, or reads
`BOT_TOKEN` from your environment or ignored `local.settings.json`. Do not paste
tokens into source code, command arguments, or chat. Temporary parameter files
have owner-only permissions and are deleted when deployment finishes.

[infra/main.bicep](./infra/main.bicep) provisions the app, queue, state table,
deployment container, and application settings. The script publishes only the
runtime files, builds Python dependencies remotely, and checks
`GET /api/health` and rejection of unauthenticated webhook calls.
It **does not change Telegram's webhook by default**.

After checking the deployment, cut over:

```bash
python deploy.py --switch-webhook
```

This registers the existing `/start` and `/tts` commands and points Telegram at
`https://<function-host>/api/telegram` with a secret header. Pending updates are
not discarded. The secret is preserved on subsequent deployments.

Before retiring an older host, test `/start`, plain text, `/tts`, a link as
literal text, and a second request within 30 seconds. Confirm audio arrives,
captions are intact, and `getWebhookInfo` reports no delivery errors.
**Only then delete the old service in its dashboard.** The script never stops
or deletes a Render service. Deleting a Render service is a separate manual
action; removing the old blueprint from this repository does not remove a
running service.

For another existing resource group, Speech resource, or supported region:

```bash
python deploy.py --resource-group <group> --speech-resource <speech-name> --location <region>
```

The script requires an authenticated Azure CLI session and a valid bot token
before provisioning anything. A failed readiness check stops before webhook
cutover. To inspect provisioning failures, use the Azure portal's resource group
**Deployments > malayalamtts** page. For rollback, re-register the previous host's
webhook using Telegram's `setWebhook` API and that host's secret, if configured;
do not discard pending updates. Keep the old host available until Azure is tested.

### Settings

These settings are populated automatically by the deployment:

| Setting | Purpose |
|---------|---------|
| `BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_WEBHOOK_SECRET` | Validates `X-Telegram-Bot-Api-Secret-Token` |
| `SPEECH_KEY` | Existing Azure Speech resource key |
| `SPEECH_REGION` | Speech resource's region |
| `AzureWebJobsStorage` | Dedicated bot storage connection |
| `DEPLOYMENT_STORAGE_CONNECTION_STRING` | Flex deployment container connection |

Keep secrets in Azure application settings or Key Vault references, never in Git.
After rotating Speech keys, rerun deployment to refresh the bot's setting.
Changing `TELEGRAM_WEBHOOK_SECRET` also requires updating Telegram's webhook.

### Reliability, privacy, and cost

- The webhook acknowledges only after its queue output is persisted by Azure.
  Synthesis runs inside an awaited queue invocation, not after an HTTP response.
- Update IDs and cooldown decisions are stored durably and shared across
  instances. Retries reuse their original admission rather than rate-limiting
  their own request. Cooldowns use the worker's processing time.
- Failed infrastructure jobs retry up to five deliveries, two minutes apart,
  before moving to `telegram-updates-poison`. Monitor this queue in Azure Storage
  Browser; failed jobs require inspection before replaying.
- Completed updates are skipped. Delivery is still **at least once**: a crash
  after Telegram accepts audio but before completion is recorded can produce
  a duplicate. Telegram does not provide an idempotency key for `sendVoice`.
- Speech errors, including exhausted quota, are reported to the user without
  automatically resynthesizing. The voice remains `en-US-AvaMultilingualNeural`,
  using the same MP3 format and full 4,000-character input limit.
- Queued text is private to the storage account and removed after successful
  processing. Failed/poison messages can retain text until an operator deletes
  them. The queue excludes sender profiles. State stores IDs and timestamps,
  not text; a daily timer removes state unchanged for seven days.
- Speech F0's [500,000 neural characters/month](https://azure.microsoft.com/pricing/details/speech/)
  and request limits are **shared with the TTS website** when using the same
  resource. Exhausting F0 fails requests; deployment does not upgrade it.
- Functions, queue/table/blob storage, and network usage have independent
  potential charges. No always-ready instances or Application Insights are
  provisioned. Cold starts are possible. Budget alerts are not spending caps.

### Local development

Install [Azure Functions Core Tools v4](https://learn.microsoft.com/azure/azure-functions/functions-run-local)
and [Azurite](https://learn.microsoft.com/azure/storage/common/storage-use-azurite).
Copy [local.settings.example.json](./local.settings.example.json) to
`local.settings.json`, fill its token, webhook secret, and Speech settings,
and keep that file untracked.

Start Azurite in another terminal. Create the local queue and table once:

```bash
az storage queue create --name telegram-updates --connection-string 'UseDevelopmentStorage=true'
az storage table create --name TelegramBotState --connection-string 'UseDevelopmentStorage=true'
func start
```

Local routes are `http://localhost:7071/api/health` and
`http://localhost:7071/api/telegram`. Calls to the latter require the configured
secret header and a Telegram update with `update_id`, `message_id`, chat ID,
and text. Local synthesis still uses real Azure Speech quota, and valid bot
credentials send real Telegram messages.

## Project Structure

```
function_app.py      — Azure HTTP, queue, and cleanup timer triggers
main.py              — Commands, input validation, and cooldown handling
storage.py           — Durable update claims and atomic cooldown decisions
telegram_client.py   — Telegram Bot API calls using a per-invocation HTTP client
tts.py               — Azure Speech, progress updates, and audio cleanup
deploy.py            — Provisioning, packaging, readiness checks, optional cutover
infra/main.bicep     — Dedicated Python Flex Consumption infrastructure
```

Each worker invocation shares one HTTP client between Telegram and Speech,
then closes its connection pool. Bot commands are registered at deployment,
not on every cold start.

## Tests

After installing dependencies, run the regression tests without contacting
external services:

```bash
python -m unittest discover -s tests
az bicep build --file infra/main.bicep --stdout > /dev/null
```

For integration tests against a local Azurite table endpoint, set
`AZURITE_TABLE_URL`, for example `http://127.0.0.1:10002/devstoreaccount1`, and run
the same test command. These tests create and delete uniquely named tables,
exercise actual conditional writes and transactions, and mock Speech and Telegram
so no live messages or Speech charges occur. They are skipped by default.

## Tech Stack

- **Azure Functions** — authenticated webhook, queue worker, cleanup timer
- **Azure Storage Queues and Tables** — durable jobs, deduplication, cooldowns
- **Azure Speech** — official neural text-to-speech
- **Telegram Bot API** — messaging
