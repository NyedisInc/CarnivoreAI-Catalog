# clean your tv

A Carnivore plugin (kind cplug) that debloats a Sony Google TV over ADB in checked batches, keeps an exact undo, and closes ADB when you are done.
A packaging of [Gathin23/cleanyourtv](https://github.com/Gathin23/cleanyourtv) (MIT), pinned at commit `204143007f0bebfb14d6ef001a32e5836b1c90b5`.
Vendored from upstream: the two Sony VH21 package lists, the core-package patterns, the Home / input / settings guards, the first-write-wins undo and the
port-closed probe. Not shipped: the launcher, themes, addons, `manage.py`, any script that runs a shell.

`"trust": "unrestricted"`: the plugin runs with no sandbox so it can open the TV's ADB port itself (pure Python `adb_shell`, vendored; no adb binary). The owner
must approve it. It contacts only the TVs you add by exact private IP. Nothing is scanned.

## The rules (refusals in code, each with a test)
- Pairing mints one key per TV in the plugin's data folder (mode 0600). It never reads or writes the home-folder adb key.
- `pm disable-user --user 0` only. No uninstall. Must-stay packages, core Android packages and Wi-Fi/Bluetooth packages are never batched.
- Each package's prior state is recorded before the first command (first write wins). Restore replays that record and nothing else; with no record it refuses.
- No reboot until 75 s after the last package change; then `sync`; then reboot.
- The next batch (and the setupwraith step) stay locked until the post-reboot checks pass: adb back, network up, Cast 8009, Play Store, Settings opens,
  input apps enabled, Home resolves, nothing reverted. "Could not determine" blocks like a failure.
- setupwraith is a separate step, never a batch line. Ethernet: no saved Wi-Fi networks. Wi-Fi: no network owned by setupwraith. Needs a tick that Cast will stop.
- Close-out, in order: revoke the authorizations (you, on the TV), verify the revoke (the TV must re-challenge a signature-only attempt), verify port 5555
  is closed, delete the TV's key. It stops at the first step that does not pass.

## UI and agents
One panel view (`tvs`): TV list, add, pair, batches, reboot countdown, check results, restore, setupwraith, close-out. Agents get two read-only tools,
`tv_status` and `tv_checks`. Routes answer HTTP 200 with `ok: false` and a reason on a refusal, because the view bridge turns any non-2xx into a bare "failed".

## Data
`<plugin files folder>/cyt-data/` (override `CYT_DATA_DIR`): `tvs/<id>.json` (identity, baseline, undo, journal, checks) and `keys/<id>/adbkey`.
