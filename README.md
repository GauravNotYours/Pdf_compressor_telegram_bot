# Telegram PDF Compressor

A private Telegram bot that accepts PDFs, rasterizes each page using PyMuPDF and Pillow, stores the compressed output in MongoDB GridFS, and sends the output back to the sender.

The original PDF is processed in memory and is never saved to MongoDB. GridFS stores the compressed PDF bytes along with its required file/chunk records. The compression process turns pages into JPEG images, so the resulting PDF is not searchable and does not retain interactive forms, links, or digital signatures.

## 1. Create a Telegram bot

1. Open Telegram and start a chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts.
3. Copy the bot token. Keep it private; do not commit it or share it.

## 2. Add allowed Telegram usernames

Add each person's Telegram username to `ALLOWED_USERNAMES`, separated by commas and without the `@`. For example, `ALLOWED_USERNAMES=alice,example_user`. Usernames are matched without regard to capitalization. Each allowed user must have a Telegram username set. The bot refuses to start if this setting is empty or invalid, and accepts documents only in private chats.

## 3. Create the MongoDB database

1. Create a MongoDB Atlas cluster, or use another MongoDB deployment accessible from Render.
2. Create a database user and allow the bot's host to connect. For Atlas, configure Network Access appropriately for your deployment.
3. Copy the MongoDB connection string. URL-encode special characters in the database username or password.
4. The bot writes compressed PDFs to a GridFS bucket in the database specified by `MONGODB_DATABASE`. It does not upload original PDFs.

## 4. Run locally

Use Python 3.11. From this project directory, create and activate a virtual environment, then install dependencies:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set your real values:

```dotenv
BOT_TOKEN=your_bot_token
ALLOWED_USERNAMES=your_telegram_username,another_username
MONGODB_URI=your_mongodb_connection_string
MONGODB_DATABASE=pdf_compressor
```

Start the bot locally (it uses Telegram long polling when `WEBHOOK_URL` and `RENDER_EXTERNAL_URL` are unset):

```powershell
python first.py
```

In Telegram, open a private chat with your bot and send `/start`, then upload a PDF as a document. Send `/stop` to disable the bot for your account until you send `/start` again. The bot accepts input PDFs up to 20 MB and sends compressed PDFs up to Telegram's 50 MB document limit.

## 5. Deploy on Render Free

This project includes a `render.yaml` Blueprint for a Render **Free Web Service**. In production, the bot switches to Telegram webhooks and serves `/health` for Render health checks. Render's free web services can spin down after inactivity, so the first request after a sleep may take longer while the service starts.

1. Push this project to a GitHub repository. Ensure `.env` is not committed; `.gitignore` excludes it.
2. In Render, choose **New + → Blueprint**, connect the repository, and deploy the Blueprint. It creates a Free Web Service and uses Render's `PORT` and `RENDER_EXTERNAL_URL` environment variables automatically.
3. Add the values requested for `BOT_TOKEN`, `ALLOWED_USERNAMES`, `MONGODB_URI`, and `WEBHOOK_SECRET` in the Render service's environment settings. `MONGODB_DATABASE` is set to `pdf_compressor` by the Blueprint and can be changed there.
4. Generate a webhook secret locally with `python -c "import secrets; print(secrets.token_urlsafe(32))"` and paste the result into Render's `WEBHOOK_SECRET` environment variable. Do not share or commit this secret.
5. Confirm the deployment is healthy and the logs show `Telegram webhook server started`. Then message the bot privately and send `/start`.

If you do not use the Blueprint, create a **Web Service** manually with build command `pip install -r requirements.txt`, start command `python first.py`, and health check path `/health`. Set the same environment variables. Keep only one running bot instance for a Telegram token.

## Configuration and limits

- `BOT_TOKEN`: token from BotFather.
- `ALLOWED_USERNAMES`: comma-separated Telegram usernames without `@`. Only these users can use the bot. Usernames can change, so update the allowlist if an authorized user changes theirs.
- `WEBHOOK_SECRET`: a private string used to verify Telegram webhook requests. Required when running in webhook mode.
- `WEBHOOK_URL`: optional public base URL override. On Render the bot uses `RENDER_EXTERNAL_URL` automatically; locally, leave this unset to use polling.
- `PORT`: Render's listening port, assigned automatically.
- `MONGODB_URI`: MongoDB connection string.
- `MONGODB_DATABASE`: database name.
- Compression uses 150 DPI rendering and JPEG quality 70. Rasterizing can reduce file size but trades away searchable text and vector quality.
- The database retains every compressed PDF uploaded to it. Configure retention/cleanup in MongoDB if you do not want files kept indefinitely.
