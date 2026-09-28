---
name: media
description: "Media tool — probe documents/audio/video/images, PDF operations, and table docs (xlsx/csv/sqlite)."
---

# Media

The **media** tool handles **documents, audio, video, images, PDFs, and
table documents** (the Operator's 08-12 spec). Probing via ffprobe, magic
bytes, and Pillow; PDF ops via pypdf; tables via openpyxl + stdlib.

## Tools

- `probe` / `info` / `duration` / `document` — inspect any file
- `image` — rich image info (Pillow: dimensions, mode, format, EXIF,
  animation)
- `pdf` — PDF operations: `info` (pages/metadata), `text` (extract a
  page), `merge` (combine PDFs), `split` (one file per page)
- `table` — row/column docs: xlsx, csv, sqlite (columns, rows, sample)

## Usage

```
media {"action": "probe", "path": "song.mp3"}
media {"action": "probe", "path": "movie.mp4"}
media {"action": "image", "path": "photo.jpg"}
media {"action": "pdf", "path": "report.pdf", "pdf_action": "info"}
media {"action": "pdf", "path": "report.pdf", "pdf_action": "text", "page": 1}
media {"action": "pdf", "path": "a.pdf", "pdf_action": "merge", "paths": ["b.pdf"], "out": "merged.pdf"}
media {"action": "pdf", "path": "report.pdf", "pdf_action": "split", "out": "/tmp/pages"}
media {"action": "table", "path": "data.xlsx"}
media {"action": "table", "path": "data.csv"}
media {"action": "table", "path": "db.sqlite", "table": "users"}
```

## When to use

- The operator asks about a media/document/image file's properties.
- PDF work (pages, text, merge, split).
- Row/column documents (Excel/CSV/SQLite).

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/media.py` — registers `media`.

---
---
