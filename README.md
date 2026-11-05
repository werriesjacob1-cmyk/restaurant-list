# restaurant-list
Generic job-board watcher. Polls public job feeds and sends push alerts for new matching roles.

Secrets: NTFY_TOPIC (required); TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ADZUNA_APP_ID, ADZUNA_APP_KEY (optional).

Run `python watch.py --selftest` (or the workflow with selftest=true) to send a test alert.
