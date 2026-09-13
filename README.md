# Philip-Bong

## Telegram listing synchronization

The `Sync Telegram listings` GitHub Actions workflow checks for new channel
posts every 15 minutes. Scheduled runs are incremental; the one-time public
history import is available only through the manual `bootstrap` option.

### Owner setup

1. In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.
2. Set the name to `TELEGRAM_BOT_TOKEN` and paste the token issued by BotFather. Do not add the token to a file, issue, commit, or workflow log.
3. Open **Actions → Sync Telegram listings → Run workflow**. Leave `bootstrap` unchecked for a normal incremental run; select it only to import the publicly available channel history.

The workflow uses the repository's encrypted secret only for the sync command.
It commits only changed files under `data` and `assets/telegram`.

### Recovery and revocation

If the token could have been exposed, revoke it with BotFather immediately,
create a replacement token, and update the `TELEGRAM_BOT_TOKEN` repository
secret using the setup path above. Do not put either token in the repository.

To pause synchronization, disable the `Sync Telegram listings` workflow from
the repository's Actions page. To roll back published generated content,
revert the relevant synchronization commit (limited to `data` and
`assets/telegram`) and push the revert. Re-enable the workflow only after the
cause is fixed; the static site continues serving the last committed version
while it is disabled.
