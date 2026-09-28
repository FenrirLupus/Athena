---
name: archive
description: "Zip/tar archives — list, extract (safe), create."
---

# Archive

The **archive** tool handles ZIP and other compressed files (tar, gz,
bz2, xz — via Python stdlib). the Operator's 08-12 spec: archive handling
for zip and other compressed files.

## Tools

- `archive_list` — list an archive's contents
- `archive_extract` — extract into a directory (safe: no zip-slip)
- `archive_create` — create a zip from files

## Usage

```
archive_list {"path": "files.zip"}
archive_extract {"path": "files.zip", "dest": "/tmp/out"}
archive_create {"path": "bundle.zip", "files": ["a.txt", "b.txt"]}
```

## Safety

Extraction paths are confined to the destination directory — archive
entries that try to escape (zip-slip) are refused.

## When to use

- The operator wants to see inside / extract / create archives.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/archive.py` — registers the archive tools.

---
---
