# FOX Camera Assist automatic updates

Independent camera assistant channel. Do not change `fox-center/` or the root FOX Producer app for camera updates.

- `src/`: camera companion source and tests (1.1.0).
- `latest.json`: version pointer consumed by installed clients.
- `releases/1.1.0/package.json`: immutable code package; no user data.
- `build_release.py`: reproducible package + one-run Windows installer build.

The user's first setup file is run once on the main Windows desktop. It preserves
LocalAppData/FOX_Camera_Assist configuration, model and learned clothes, and creates
a Desktop launcher. OBS laptop operators keep using the existing camera room URL.
The initial setup leaves an already running camera assistant alone; the user switches
to the new Desktop launcher after the broadcast. No changes to OBS Studio itself.

Each startup checks the fixed HTTPS channel, verifies package hashes, and starts the
selected slot. While running, the companion checks hourly and downloads only.
An update is activated at the next launch, never during a broadcast session.
A failed start rolls back the configuration snapshot and returns to the prior slot.
An offline startup uses the installed or previously verified downloaded release.
Camera-image switching still requires the existing explicit per-change consent.

## Publishing

1. Change VERSION in src/auto_update.py, bootstrap/server/UI labels, tests and build_release.py.
2. Run `python -m unittest discover -s src/tests -v` with requirements installed.
3. Run `python build_release.py`.
4. Add release/package.json under a NEW releases/<version>/ path and update latest.json atomically.
5. Do not modify packages for released versions. Keep old release slots available.

The channel trusts HTTPS and this GitHub repository. SHA-256 detects corruption;
it is not an independent signing authority. No client credentials are required.
The release contains only program files; cameras, products, prices and passwords stay local.

Validated: 43 tests plus isolated installer migration and actual local server startup.
The Windows command wrapper and user's live devices require field confirmation.
