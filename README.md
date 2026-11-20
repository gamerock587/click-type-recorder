<div align="center">

# Click & Type Recorder

### Record repeatable desktop workflows on Windows

![Source release](https://img.shields.io/badge/release-source-2563eb?style=flat-square)
![Local first](https://img.shields.io/badge/design-local--first-0f766e?style=flat-square)

**Coded by [zaid.dev](https://github.com/gamerock587)**

Python · Tkinter · Windows

</div>

---

## What it does

- Click and keyboard recording with stop hotkey
- Account-list and repeat playback
- Optional user-configured Telegram status

## Quick start

**Requirements:** Windows and Python 3.11+.

```powershell
pip install -r requirements.txt
python recorder.py
```

## Before you run

No accounts, passwords, Telegram credentials, personal recordings or saved progress are supplied. Create your own accounts.txt if needed. Recordings can contain typed secrets: review them before sharing. F9 stops playback. Game registration helpers are experimental and must comply with the target service rules.

## Documentation

See [USAGE.md](USAGE.md) for the original detailed operating guide.

## Privacy and configuration

This source export excludes local credentials, databases, sessions, logs, recordings,
personal spreadsheets and compiled distributions. Supply your own configuration.
The ignore rules help prevent accidental commits; they do not replace reviewing files.

## Contributing

Describe the problem, include reproducible steps with synthetic data, and keep changes focused.
Never include tokens, private recordings, customer data or personal contact lists in an issue.
Validation and remaining limitations are recorded in [RELEASE_REVIEW.md](RELEASE_REVIEW.md).

## License and attribution

Original application code is licensed under [MIT](LICENSE). Third-party components retain
their own licenses and authorship; see THIRD-PARTY-NOTICES.md and any bundled license files.
