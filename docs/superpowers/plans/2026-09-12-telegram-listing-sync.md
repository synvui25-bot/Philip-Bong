# Telegram Listing Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically synchronize qualifying property posts from `@sarawakpropertyguru` into a paginated, gallery-enabled section beneath the website's three curated listings.

**Architecture:** A dependency-light Python synchronizer converts Telegram posts into deterministic JSON and optimized local media. GitHub Actions runs it every 15 minutes, commits changed generated files, and GitHub Pages serves a static client that renders nine safe listing cards at a time.

**Tech Stack:** Python 3.12 standard library, Pillow, pytest, Telegram Bot API, GitHub Actions, vanilla HTML/CSS/JavaScript, GitHub Pages

**Spec:** `docs/superpowers/specs/2026-09-12-telegram-listing-sync-design.md`

## Global Constraints

- Preserve the three manually curated featured listings above the synchronized section.
- Display nine Telegram listings initially and reveal nine more per **Load More** click.
- Never guess missing property fields.
- Store `TELEGRAM_BOT_TOKEN` only as an encrypted GitHub Actions secret; never publish or log it.
- Treat Telegram text as untrusted and render it with DOM text properties, never `innerHTML`.
- Keep the existing 375-pixel mobile layout, keyboard gallery controls, lazy loading, SEO, and WhatsApp CTA behavior.
- Leave the last valid generated site unchanged when Telegram is temporarily unavailable.
- Do not commit when synchronization output is unchanged.

---

### Task 1: Deterministic Property Post Parser

**Files:**
- Create: `scripts/telegram_parser.py`
- Create: `tests/fixtures/property_posts.json`
- Create: `tests/test_telegram_parser.py`

**Interfaces:**
- Consumes: Telegram-like dictionaries containing `message_id`, `date`, `text` or `caption`, `photo`, and optional `media_group_id`.
- Produces: `is_listing(text: str, has_photo: bool) -> bool` and `parse_listing(post: dict) -> dict | None`.

- [ ] **Step 1: Write representative failing parser tests**

```python
def test_parses_rental_listing():
    post = {
        "message_id": 42,
        "date": 1789092000,
        "caption": "🏭 Demak Laut Warehouse For RENT\nRM27,300/month\n21,000 sq ft\nRefer code: WC",
        "photo": [{"file_id": "small"}, {"file_id": "large"}],
    }
    item = parse_listing(post)
    assert item["id"] == "telegram-42"
    assert item["status"] == "For Rent"
    assert item["price"] == "RM27,300/month"
    assert item["reference"] == "WC"
    assert item["facts"] == ["21,000 sq ft"]

def test_rejects_announcement_without_property_intent():
    assert parse_listing({"message_id": 7, "text": "Happy Malaysia Day!"}) is None
```

- [ ] **Step 2: Run the parser tests and verify RED**

Run: `python -m pytest tests/test_telegram_parser.py -v`

Expected: FAIL because `scripts.telegram_parser` does not exist.

- [ ] **Step 3: Implement explicit recognition and field extraction**

```python
PROPERTY_TERMS = re.compile(r"\b(for sale|for rent|rental|selling|house|condo(?:minium)?|shop\s?lot|warehouse|land|rumah|untuk dijual|untuk disewa)\b", re.I)
PRICE = re.compile(r"\bRM\s?[\d,.]+(?:\s*(?:/|per)\s*(?:month|bulan|psf))?", re.I)
SIZE = re.compile(r"\b(?:approx\.?\s*)?[\d,.]+\s*(?:sq\s*ft|sqft|ft²|acres?)\b", re.I)
REFERENCE = re.compile(r"(?:refer(?:ence)?\s*code|ref)\s*:\s*([\w-]+)", re.I)

def is_listing(text: str, has_photo: bool) -> bool:
    has_price = bool(PRICE.search(text) or re.search(r"price\s+on\s+application|enquire\s+for\s+price", text, re.I))
    return bool(PROPERTY_TERMS.search(text) and has_price and (has_photo or "t.me/" in text))
```

Normalize whitespace, derive title from the first meaningful line, omit absent values, retain `source_text`, and set `telegram_url` to `https://t.me/sarawakpropertyguru/{message_id}`.

- [ ] **Step 4: Run focused and full parser tests**

Run: `python -m pytest tests/test_telegram_parser.py -v`

Expected: PASS for English/Malay terms, prices, sizes, references, missing optional fields, and rejected announcements.

- [ ] **Step 5: Commit the parser deliverable**

```bash
git add scripts/telegram_parser.py tests/fixtures/property_posts.json tests/test_telegram_parser.py
git commit -m "feat: parse Telegram property listings"
```

### Task 2: Normalize Albums and Synchronize Bot Updates

**Files:**
- Create: `scripts/telegram_client.py`
- Create: `scripts/sync_telegram.py`
- Create: `tests/test_telegram_sync.py`
- Create: `data/telegram-listings.json`
- Create: `data/telegram-sync-state.json`

**Interfaces:**
- Consumes: `parse_listing(post)` from Task 1 and Bot API JSON from `getUpdates`.
- Produces: `fetch_updates(token: str, offset: int) -> list[dict]`, `group_media_posts(messages: list[dict]) -> list[dict]`, and `apply_updates(listings: list[dict], updates: list[dict]) -> tuple[list[dict], int]`.

- [ ] **Step 1: Write failing tests for new posts, edits, albums, and duplicates**

```python
def test_album_becomes_one_listing():
    messages = [
        {"message_id": 10, "media_group_id": "a", "caption": "House For Sale RM500,000", "photo": [{"file_id": "p1"}]},
        {"message_id": 11, "media_group_id": "a", "photo": [{"file_id": "p2"}]},
    ]
    grouped = group_media_posts(messages)
    assert len(grouped) == 1
    assert grouped[0]["message_id"] == 10
    assert grouped[0]["file_ids"] == ["p1", "p2"]

def test_edit_replaces_existing_record():
    current = [{"id": "telegram-10", "price": "RM500,000"}]
    updates = [{"update_id": 5, "edited_channel_post": listing_message(10, "House For Sale RM480,000")}]
    listings, offset = apply_updates(current, updates)
    assert listings[0]["price"] == "RM480,000"
    assert offset == 6
```

- [ ] **Step 2: Run sync tests and verify RED**

Run: `python -m pytest tests/test_telegram_sync.py -v`

Expected: FAIL because the client and synchronizer are absent.

- [ ] **Step 3: Implement a redacting Bot API client**

```python
def fetch_updates(token: str, offset: int) -> list[dict]:
    query = urlencode({"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["channel_post", "edited_channel_post"])})
    request = Request(f"https://api.telegram.org/bot{token}/getUpdates?{query}")
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if not payload.get("ok"):
        raise TelegramError(payload.get("description", "Telegram request failed"))
    return payload["result"]
```

Never print the request URL or token. Validate that every processed `chat.username` equals `sarawakpropertyguru`.

- [ ] **Step 4: Implement deterministic update application**

Group media by `media_group_id`, select the caption-bearing message as the stable primary ID, replace edits by stable ID, ignore already processed update IDs, and sort output by `published_at` descending then numeric message ID descending.

- [ ] **Step 5: Run synchronization tests**

Run: `python -m pytest tests/test_telegram_sync.py -v`

Expected: PASS with deterministic ordering and offset advancement only after successful processing.

- [ ] **Step 6: Commit the synchronization core**

```bash
git add scripts/telegram_client.py scripts/sync_telegram.py tests/test_telegram_sync.py data/telegram-listings.json data/telegram-sync-state.json
git commit -m "feat: synchronize Telegram channel updates"
```

### Task 3: Public-History Bootstrap, Media, and Deletion Reconciliation

**Files:**
- Create: `scripts/telegram_public.py`
- Create: `scripts/telegram_media.py`
- Create: `tests/fixtures/channel_preview.html`
- Create: `tests/test_telegram_public.py`
- Create: `tests/test_telegram_media.py`
- Modify: `scripts/sync_telegram.py`

**Interfaces:**
- Consumes: public preview HTML, normalized records from Task 2, and Bot API file identifiers.
- Produces: `parse_public_history(html: str) -> list[dict]`, `reconcile_missing(listings: list[dict], visible_ids: set[int], state: dict) -> tuple[list[dict], dict]`, and `save_webp(content: bytes, destination: Path) -> None`.

- [ ] **Step 1: Write failing history and two-strike deletion tests**

```python
def test_missing_post_requires_two_confirmations():
    listings = [{"id": "telegram-20", "message_id": 20}]
    first, state = reconcile_missing(listings, set(), {})
    assert len(first) == 1
    second, state = reconcile_missing(first, set(), state)
    assert second == []

def test_visible_post_resets_missing_count():
    listings = [{"id": "telegram-20", "message_id": 20}]
    kept, state = reconcile_missing(listings, {20}, {"missing_counts": {"20": 1}})
    assert kept == listings
    assert "20" not in state["missing_counts"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `python -m pytest tests/test_telegram_public.py tests/test_telegram_media.py -v`

Expected: FAIL because history, reconciliation, and media functions are missing.

- [ ] **Step 3: Implement bounded public-history pagination**

Read `https://t.me/s/sarawakpropertyguru`, follow only its `?before=<id>` pagination links, stop when no lower ID appears, and cap each invocation at 100 pages. Parse message IDs, timestamps, text, media URLs, and permalinks from semantic `tgme_widget_message_*` elements. Bootstrap mode runs only when invoked with `--bootstrap`.

- [ ] **Step 4: Implement safe media conversion**

```python
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
MAX_BYTES = 20 * 1024 * 1024

def save_webp(content: bytes, destination: Path) -> None:
    if len(content) > MAX_BYTES:
        raise MediaError("media exceeds 20 MB")
    with Image.open(BytesIO(content)) as image:
        image.verify()
    with Image.open(BytesIO(content)) as image:
        image.convert("RGB").thumbnail((1800, 1800))
        image.save(destination, "WEBP", quality=84, method=6)
```

Write assets to `assets/telegram/{message_id}-{index}.webp` through a temporary file followed by an atomic rename.

- [ ] **Step 5: Implement reconciliation and failure preservation**

Increment `missing_counts[id]` only after a successful full public-preview scan. Remove at count two; reset the count when visible. If any fetch or parse stage fails, exit non-zero before replacing JSON or deleting media.

- [ ] **Step 6: Run media/history tests and the complete Python suite**

Run: `python -m pytest -v`

Expected: PASS, including oversized media, invalid images, temporary failure, deterministic filenames, albums, and two-strike deletion.

- [ ] **Step 7: Commit bootstrap and reconciliation**

```bash
git add scripts/telegram_public.py scripts/telegram_media.py scripts/sync_telegram.py tests
git commit -m "feat: bootstrap and reconcile Telegram listings"
```

### Task 4: Static Latest Listings Interface

**Files:**
- Modify: `index.html`
- Create: `tests/site/telegram-listings.spec.js`
- Create: `tests/site/fixtures/telegram-listings.json`

**Interfaces:**
- Consumes: `data/telegram-listings.json` records from Tasks 1–3.
- Produces: `createTelegramCard(listing: object) -> HTMLElement`, `renderTelegramBatch() -> void`, and gallery registration for dynamically rendered cards.

- [ ] **Step 1: Write failing browser tests**

```javascript
test('renders nine safely and loads the next batch', async ({ page }) => {
  await page.route('**/data/telegram-listings.json', route => route.fulfill({ path: fixture }));
  await page.goto(siteUrl);
  await expect(page.locator('#telegramListings .listing-card')).toHaveCount(9);
  await page.getByRole('button', { name: 'Load More' }).click();
  await expect(page.locator('#telegramListings .listing-card')).toHaveCount(18);
});

test('does not interpret Telegram text as HTML', async ({ page }) => {
  await page.goto(siteUrl);
  await expect(page.locator('#telegramListings script')).toHaveCount(0);
  await expect(page.locator('#telegramListings')).toContainText('<script>');
});
```

- [ ] **Step 2: Run browser tests and verify RED**

Run: `node tests/site/telegram-listings.spec.js`

Expected: FAIL because `#telegramListings` and Load More do not exist.

- [ ] **Step 3: Add the section and responsive styles beneath Featured Properties**

Add `#telegram-listings`, a loading/empty status, `#telegramListings`, and `#loadMoreListings`. Reuse `.listing-grid`, `.listing-card`, `.listing-photo`, `.listing-content`, `.listing-facts`, and existing navy/gold button styles. Mark editable headings with `<!-- EDIT -->` while marking the card container as generated.

- [ ] **Step 4: Render using safe DOM APIs**

```javascript
const PAGE_SIZE = 9;
function text(tag, value, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = value;
  return node;
}
```

Use `textContent`, `setAttribute`, and validated `https://t.me/sarawakpropertyguru/` URLs. Build WhatsApp URLs with `URLSearchParams`/`encodeURIComponent`. Give images `loading="lazy"`, explicit dimensions, descriptive alt text, and local fallback handling.

- [ ] **Step 5: Extend the existing gallery without duplicating controls**

Register each dynamic card's image array with the existing dialog. Verify click, left/right buttons, keyboard arrows, wrapping, Escape, close button, background scroll lock, focus return, and lazy loading of non-cover images.

- [ ] **Step 6: Run browser tests at desktop and 375 pixels**

Run: `node tests/site/telegram-listings.spec.js`

Expected: PASS for 9/18 batching, empty/error states, text escaping, links, gallery behavior, and no horizontal overflow at 375 pixels.

- [ ] **Step 7: Commit the web interface**

```bash
git add index.html tests/site
git commit -m "feat: show synchronized Telegram listings"
```

### Task 5: Scheduled GitHub Actions Workflow and Secret Safety

**Files:**
- Create: `.github/workflows/telegram-listings.yml`
- Create: `requirements.txt`
- Create: `tests/test_workflow_config.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `TELEGRAM_BOT_TOKEN` repository secret and synchronization entry point `python scripts/sync_telegram.py`.
- Produces: a scheduled/manual workflow that commits only changed generated data/media.

- [ ] **Step 1: Write failing workflow-policy tests**

```python
def test_workflow_has_schedule_manual_run_and_minimal_permissions():
    workflow = Path('.github/workflows/telegram-listings.yml').read_text()
    assert "cron: '*/15 * * * *'" in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: write" in workflow
    assert "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in workflow

def test_token_is_absent_from_publishable_files():
    for path in [Path('index.html'), *Path('data').glob('*.json')]:
        assert "TELEGRAM_BOT_TOKEN" not in path.read_text(encoding='utf-8')
```

- [ ] **Step 2: Run policy tests and verify RED**

Run: `python -m pytest tests/test_workflow_config.py -v`

Expected: FAIL because the workflow is absent.

- [ ] **Step 3: Add the pinned, minimal workflow**

```yaml
name: Sync Telegram listings
on:
  schedule:
    - cron: '*/15 * * * *'
  workflow_dispatch:
    inputs:
      bootstrap:
        description: Import publicly available channel history
        required: false
        default: false
        type: boolean
permissions:
  contents: write
concurrency:
  group: telegram-listing-sync
  cancel-in-progress: false
jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
      - run: pip install --require-hashes -r requirements.txt
      - run: python scripts/sync_telegram.py ${{ inputs.bootstrap && '--bootstrap' || '' }}
        env:
          TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}
      - run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data assets/telegram
          git diff --cached --quiet || git commit -m "chore: sync Telegram listings"
          git push
```

Generate hashed Pillow requirements with `pip-compile --generate-hashes` so the shown install command is reproducible.

- [ ] **Step 4: Document owner setup and recovery**

Document exactly: repository **Settings → Secrets and variables → Actions → New repository secret**, name `TELEGRAM_BOT_TOKEN`, paste the BotFather token, then **Actions → Sync Telegram listings → Run workflow**. Include token revocation through BotFather and workflow disable/rollback instructions.

- [ ] **Step 5: Run policy and complete test suites**

Run: `python -m pytest -v`

Run: `node tests/site/telegram-listings.spec.js`

Expected: both PASS; repository search confirms no token value in tracked files or test output.

- [ ] **Step 6: Commit workflow and documentation**

```bash
git add .github/workflows/telegram-listings.yml requirements.txt tests/test_workflow_config.py README.md
git commit -m "ci: schedule Telegram listing synchronization"
```

### Task 6: Bootstrap and Live Acceptance

**Files:**
- Generated: `data/telegram-listings.json`
- Generated: `data/telegram-sync-state.json`
- Generated: `assets/telegram/*.webp`
- Modify only if defects are found: files owned by Tasks 1–5

**Interfaces:**
- Consumes: the administrator bot, encrypted repository secret, public channel history, and completed workflow.
- Produces: a verified live `https://synvui25-bot.github.io/Philip-Bong/` Telegram listing section.

- [ ] **Step 1: Confirm repository prerequisites without exposing credentials**

Verify that the bot is an administrator of `@sarawakpropertyguru` and the repository reports a secret named `TELEGRAM_BOT_TOKEN`. Do not request, echo, or log the token value.

- [ ] **Step 2: Run the bootstrap workflow manually**

Trigger `Sync Telegram listings`, select the `bootstrap` checkbox, and confirm the run completes and commits generated listings/media.

- [ ] **Step 3: Verify generated integrity**

Run: `python -m pytest -v`

Run: `node tests/site/telegram-listings.spec.js`

Expected: PASS, with valid JSON, existing local image files, newest-first ordering, and zero token matches.

- [ ] **Step 4: Verify the live site**

Open `https://synvui25-bot.github.io/Philip-Bong/` at 375 pixels and desktop width. Confirm three curated cards remain first; nine Telegram cards render initially; Load More reveals nine; Telegram and WhatsApp links target the correct listing; galleries load and close accessibly.

- [ ] **Step 5: Verify edit and deletion behavior with a controlled test post**

Publish a clearly labeled test property post, run/wait for sync, and confirm it appears. Edit its price and confirm the card updates. Delete it and run two successful reconciliation cycles; confirm the card disappears while all other listings remain.

- [ ] **Step 6: Commit any deterministic bootstrap output**

```bash
git add data assets/telegram
git diff --cached --quiet || git commit -m "chore: bootstrap Telegram listings"
git push
```

- [ ] **Step 7: Record acceptance evidence**

Record the workflow run URL, deployment URL, counts imported/skipped, test results, and the controlled edit/deletion result in the delivery summary. Do not include the bot token.

