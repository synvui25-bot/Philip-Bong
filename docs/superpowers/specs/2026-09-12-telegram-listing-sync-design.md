# Telegram Listing Sync Design

## Purpose

Automatically publish property listings from the public Telegram channel `@sarawakpropertyguru` beneath the existing Featured Properties section on Philip Bong's GitHub Pages landing page. New posts, edits, and deletions should reach the website without exposing Telegram credentials or requiring Philip to edit HTML.

## Scope

This feature will:

- Import the publicly available channel history once.
- Synchronize new, edited, and deleted channel posts every 15 minutes.
- Display nine listings initially and reveal nine more per click of **Load More**.
- Give every listing a WhatsApp enquiry CTA, original Telegram link, and full-screen photo gallery.
- Preserve the three manually curated featured listings above the synchronized section.
- Skip posts that cannot be confidently classified as property listings.

It will not provide an administrative dashboard, accept website payments, modify Telegram posts, or use generative AI to invent missing property information.

## Architecture

The repository will remain a static GitHub Pages site. A scheduled GitHub Actions workflow will run a small synchronization program every 15 minutes and whenever manually dispatched.

The program will combine two sources:

1. A one-time bootstrap reader for the public channel preview, used only to collect history that predates the bot.
2. Telegram Bot API updates for posts received after the bot is added as a channel administrator. These updates provide new and edited channel posts; deletion handling is performed separately through public-post reconciliation.

Normalized listing records and processed-update state will be stored in versioned JSON. Downloaded listing images will be stored as optimized WebP assets. When data changes, the workflow will regenerate the synchronized listing markup/data, commit the changed generated files, and allow the existing GitHub Pages deployment to publish them.

## Repository Components

- `scripts/sync_telegram.py`: fetches updates, parses posts, reconciles state, downloads media, and writes deterministic output.
- `data/telegram-listings.json`: normalized listing records used by the page.
- `data/telegram-sync-state.json`: last processed Telegram update identifier and post reconciliation metadata.
- `assets/telegram/`: optimized listing images named by channel post ID and image index.
- `.github/workflows/telegram-listings.yml`: scheduled and manual synchronization workflow.
- `index.html`: adds the generated Telegram Listings section and client-side Load More/gallery behavior.

Generated content will remain clearly marked so hand edits are not overwritten outside the synchronized section.

## Listing Recognition and Parsing

A post qualifies as a listing only when it contains all of the following:

- It contains at least one property-intent term such as `for sale`, `for rent`, `selling`, `rental`, `house`, `condominium`, `shoplot`, `warehouse`, `land`, or common Malay equivalents.
- It includes a usable location or property name.
- It includes a price, `price on application`, or an explicit instruction to enquire for price.
- It contains at least one photo or a usable Telegram post link.

The parser will extract, when present:

- title/property name;
- sale or rental status;
- price and negotiability;
- location;
- property type;
- bedrooms, bathrooms, car parks, land size, and built-up area;
- highlights and descriptive text;
- reference code;
- Telegram message ID, publication time, edit time, permalink, and image paths.

Missing fields will be omitted rather than guessed. The original post text will be retained in the JSON for traceability. Parsing will use explicit patterns and labels so output is repeatable and reviewable.

## Telegram Albums and Media Groups

Telegram albums may arrive as multiple messages with a shared media-group identifier. The synchronizer will group those messages into one listing, order photos consistently, and use the caption-bearing message as the primary record. Duplicate photos and duplicate posts will be ignored using stable Telegram identifiers and file hashes.

The first image will be used as the card image. All images will be lazy-loaded, optimized to WebP, and included in the existing accessible lightbox carousel.

## Edits and Deletions

Edited channel posts will replace the normalized fields and media for the matching Telegram message ID on the next run.

Telegram bot updates do not guarantee a recoverable deletion event in every situation. To meet the requested deletion behavior reliably, each scheduled run will also reconcile known public post permalinks. A record will be removed after the corresponding public post is confirmed unavailable on two consecutive runs. This avoids removing listings during a temporary Telegram outage.

If a post changes so that it no longer qualifies as a listing, it will be removed from the website dataset.

## Website Experience

A new section titled **Latest from Telegram** will appear immediately below the three Featured Properties cards.

Each card will show:

- cover photo and photo count;
- For Sale/For Rent badge;
- property title and location;
- price;
- available structured facts;
- concise description;
- **Enquire on WhatsApp** CTA prefilled with the property title and reference/post ID;
- **View original post** link.

The newest nine listings will render initially. **Load More** will reveal the next nine without navigating away or reloading the page. The button disappears when every listing is visible. Empty state copy will direct visitors to the Telegram channel if no synchronized listings are available.

The existing mobile-first styling, keyboard-operable gallery, Escape-to-close behavior, focus handling, and 375-pixel layout requirements will be preserved.

## Security

- `TELEGRAM_BOT_TOKEN` will be stored only as an encrypted GitHub Actions secret.
- The token will never be committed, rendered into HTML, logged, or sent to browser JavaScript.
- The workflow will have only the repository permissions needed to update generated files.
- The bot should receive minimal channel administrator privileges and should not be allowed to add administrators or manage subscribers.
- External text will be treated as untrusted data and escaped before becoming HTML.
- Media downloads will enforce file-type and size limits.

## Failure Handling

- A Telegram timeout or temporary failure will leave the last valid website data unchanged.
- A malformed post will be logged and skipped without stopping other listings.
- An image-download failure will retain the existing image where possible and otherwise use a local property placeholder.
- The workflow will not commit when generated output is unchanged.
- Consecutive workflow failures will be visible in the GitHub Actions page; the live site will continue serving the last successful version.
- The synchronization script will redact tokens and sensitive request parameters from logs.

## Initial Setup

Philip will create the bot with `@BotFather`, add it as an administrator to `@sarawakpropertyguru`, and store its token in the repository secret named `TELEGRAM_BOT_TOKEN`. A manually triggered bootstrap run will import the public history. Normal scheduled runs will then process subsequent changes.

## Testing and Acceptance Criteria

Automated tests will cover:

- listing and non-listing classification fixtures;
- extraction of common English and Malay property fields;
- albums, duplicates, edits, and deletion reconciliation;
- safe HTML escaping and malformed input;
- deterministic JSON ordering and unchanged-output behavior;
- nine-at-a-time Load More behavior;
- mobile width without horizontal overflow;
- gallery open, next, previous, wrap, close, keyboard, and lazy-loading behavior;
- correct WhatsApp and Telegram links;
- absence of the bot token from all published output.

The feature is accepted when a test Telegram listing appears on the live page after a scheduled/manual run, an edit is reflected, a confirmed deletion is removed, all photos work in the gallery, and the existing three featured listings remain unchanged.

## Rollback

Disable the GitHub Actions workflow and revert the generated data, assets, and synchronized section. The manually curated landing page and its three featured listings will continue to work independently.

