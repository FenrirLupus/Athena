---
name: notification
description: "Notification — desktop popups to the operator (system)."
---

# Notification

The **notification** tool sends desktop popups to the operator — the
agent's way to NOTIFY the operator what's going on (the Operator's 08-12
spec). Uses notify-send on the Linux desktop.

## Tools

- `notify` — send a desktop notification (title, body, urgency)

## Usage

```
notify {"title": "Build done", "body": "The doctor is green.", "urgency": "normal"}
notify {"body": "Backup completed"}
```

## When to use

- A long task finished.
- The operator needs attention (critical).
- The agent wants to announce something without a chat message.

## References

- `references/` — (empty; the tool is self-contained)

## Scripts

- `scripts/notification.py` — registers `notify`.

---
---
