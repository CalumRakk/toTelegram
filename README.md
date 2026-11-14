[Leer en español](README.es.md)

# toTelegram

`toTelegram` turns Telegram into a private, clean, and unlimited cloud storage system.

It is designed to let you upload massive files or entire directories, keep track of what is already backed up, and restore exactly what you need at any time.

---

## Why toTelegram?

* **Upload any file, regardless of its size:** Forget about Telegram's 2 GB or 4 GB per-file limit. You can upload 20 GB videos, disk images, or heavy projects; when downloaded, they will be restored intact.
* **Full directories with selective download:** Back up entire folders while preserving their original structure. If you ever need just a specific photo or document from that folder later on, you can download it immediately without fetching gigabytes of the entire backup.
* **Save time and bandwidth:** If your internet connection drops or you cancel the process, uploads resume right where they left off. It also automatically identifies previously uploaded files so you never upload the same data twice.
* **Instant duplication between channels:** Need a copy of your backups in another channel or want to share them? Clone them in seconds directly within Telegram—zero bandwidth usage from your machine and no re-uploading required.
* **Safely reclaim disk space:** Easily identify which local files are 100% backed up to Telegram so you can delete them from your computer with total confidence that you can restore them whenever needed.

---

## Installation

Requires **Python 3.10** or higher.

```bash
pip install totelegram
```

---

## Getting Started

### 1. Log in

Run the interactive guided assistant to connect your Telegram account:

```bash
totelegram login
```

The program will prompt you for your phone number and the verification code sent to your Telegram app.

*(Optional: If you have your own `API_ID` and `API_HASH` credentials from [my.telegram.org](https://my.telegram.org), you can provide them during the setup).*

### 2. Check connection

You can verify which account is currently active at any time:

```bash
totelegram status
```

---

## Common Usage

For all sending or backup operations, specify the destination using the `-t` or `--target` flag. You can use:
* `me` to send to your **Saved Messages** (ideal for quick, personal storage).
* The title or `@username` of a **Channel** (e.g., `-t "My Backups"` or `-t @my_channel`).
* The numeric ID of the channel (e.g., `-t -100123456789`).

---

### `send` vs `backup`: When to use each

| Command | Best for... | How it appears on Telegram |
| :--- | :--- | :--- |
| **`send`** | **Standalone files** (movies, installers, PDFs, large videos). | Each file is posted as an independent message in the chat. |
| **`backup`** | **Entire directories** (projects, photo albums, document archives). | Everything is packaged into a single organized backup without cluttering the chat, preserving folder hierarchies. |

---

### Sending individual files (`send`)

Upload one or more files to Telegram. If a file exceeds Telegram limits, it will be automatically split without losing integrity.

```bash
# Send a large file to your Saved Messages
totelegram send movie.mkv -t me

# Send multiple files to a channel
totelegram send video.mp4 installer.iso manual.pdf -t "My Cloud"

# Send all files from a directory
totelegram send movies_1080p/ -t "My Cloud"
```

> ⚠️ **Caution with directories in `send`:**
> Passing a directory to `send` will upload every internal file as a separate message. **Do not use `send` for directories containing hundreds or thousands of small files** (such as source code or photos), as it will flood your channel with thousands of individual messages. For directories, always use `backup`.

---

### Backing up entire directories (`backup`)

Designed specifically for directories. Packages the entire hierarchy into an optimized data container while preserving original file names, subdirectories, and timestamps.

```bash
# Back up an entire folder to a channel
totelegram backup ./my_photos -t "Family Backup"
```

* **Clean channel:** Even if your folder contains 50,000 files, it will not flood the chat; it generates only a few compact, clean chunks.
* **Selective extraction:** Backing up with `backup` allows you to download a single file from the folder in the future without downloading the entire archive.

---

### Downloading and restoring (`download`)

To restore a backup, you only need the manifest file (`.ttape` or `.json.xz`):

```bash
# Restore an entire folder or file
totelegram download backup.ttape -o ./restored

# Surgical extraction: download a single file from a backed-up folder
totelegram download backup.ttape --file "photos/trip/beach.jpg" -o ./destination
```

---

### Checking local status (`status`)

To check which files in a directory are already backed up to Telegram and which are still pending:

```bash
totelegram status ./my_documents
```

Displays a breakdown of each file's status: backed up, locally modified, or pending upload.

---

### Replicating to another channel (`forward`)

If you have already backed up a file or folder to one channel and want a copy in another, clone it server-side without transferring data from your computer:

```bash
totelegram forward backup.ttape -t "Secondary Channel"
```

---

### Reclaiming disk space (`prune`)

Safely delete local files that are already 100% confirmed and backed up to Telegram, keeping the local index intact so you can download them whenever needed:

```bash
# Preview what would be deleted without touching disk
totelegram prune ./my_videos --dry-run

# Reclaim space by confirming deletion
totelegram prune ./my_videos
```

---

## Useful Options

* **Resume or force upload (`--force`):**
  If an upload was interrupted or you want to restart while ignoring previous states:
  ```bash
  totelegram send file.zip -t me --force
  ```
* **Default target via environment variable:**
  Set `TOTELEGRAM_TARGET` so you don't have to specify `-t` on every command:
  ```bash
  export TOTELEGRAM_TARGET="My Backups"
  totelegram send file.iso
  ```
* **Multi-account management:**
  Switch between saved Telegram accounts at any time:
  ```bash
  totelegram switch
  ```
