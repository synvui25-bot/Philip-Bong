# Philip-Bong

## Telegram listing synchronization

The `Sync Telegram listings` GitHub Actions workflow checks for new channel
posts every 15 minutes. Scheduled runs are incremental; the one-time public
history import is available only through the manual `bootstrap` option.

### Owner setup

1. In the repository, open **Settings → Secrets and variables → Actions → New repository secret**.
2. Set the name to `TELEGRAM_BOT_TOKEN` and paste the token issued by BotFather. Do not add the token to a file, issue, commit, or workflow log.
3. Open **Settings → Pages → Build and deployment** and set **Source** to **GitHub Actions**. Allow the repository's default branch to deploy to the `github-pages` environment if environment restrictions are enabled.
4. Open **Actions → Sync Telegram listings → Run workflow** on the default branch. Leave `bootstrap` unchecked for a normal incremental run; select it only to import the publicly available channel history.

The workflow uses the repository's encrypted secret only for the sync command.
It commits only changed files under `data` and `assets/telegram`.
After every successful synchronization, including runs without changes, it
uploads the static site and explicitly deploys it to GitHub Pages. The artifact
contains the root HTML/WebP files, assets, and public listings JSON. Sync state,
scripts, repository metadata, and secrets are excluded. This explicit deployment
is required because commits made with `GITHUB_TOKEN` do not trigger a Pages build.

### Recovery and revocation

If the token could have been exposed, revoke it with BotFather immediately,
create a replacement token, and update the `TELEGRAM_BOT_TOKEN` repository
secret using the setup path above. Do not put either token in the repository.

To pause synchronization, disable the `Sync Telegram listings` workflow from
the repository's Actions page. To roll back published generated content,
revert the relevant synchronization commit (limited to `data` and
`assets/telegram`) and push the revert. Once the cause is fixed, re-enable the
workflow and run it to publish the corrected site. A push alone does not deploy
this custom workflow. The site continues serving its last successful deployment
while the workflow is disabled or fails.

