# MG-300 MKII editor — handoff (2026-11-06, end of session 1)

## Verified on hardware (details: research/FACTS.md — source of truth)
- Read: identity, tempo (03), current (15), block order (0D), edit buffer per scene (0C [p s]), stored presets (0B p s).
- Live edit via CC (CC12–77 → scene offsets, swept), block switch CC1–11, PC preset select, PC-to-current = revert.
- SAVE: 3× `0B 01 p s <enc>` + commit `07 01 p p`, readback identical. Tested ONLY on user 3B = PC 9, restored byte-exact
  (backups in research/backup/*.READONLY.json).
- Scene switch = CC80 `B0 50 <s>` (see correction below; `15 01 p s` only sets the plain-0C "view" part).
  Tempo set `03 01 hi lo x` = live tempo + 0x5B/0x5C of the active part (no 15 after it). Block order set `0D 01 <10 ids>`.
- Preset numbering: 4 slots/bank A–D; PC 0–35 user 1A–9D, 36–71 factory 1A–9D. Only test writes on USER presets, never factory.

## App state (2026-11-07; `python3 -m unittest` = 162 OK; ./mg300-editor [--fake] [--monitor])
- Features, all wired (menu + shortcuts, enabled only when meaningful): Mentés Ctrl+S (factory -> Save As), Mentés másként
  Ctrl+Shift+S (user slots only, "Felülírod: 3B …?"), Átnevezés F2, Scene másolása, Importálás slotba, import toast
  "Mentés…", auto-backup ~/.local/share/mg300edit/backups (max 200), scene 1/2/3 (+ keys 1/2/3) via CC80, tempo spin
  (debounced) + TAP, chain drag&drop / Ctrl+←/→ -> 0D set, modified pill + suggested Save button + unsaved-changes dialog,
  revert (CC80 back to the user's scene), developer mode (settings.json). Controller follows the 2026-11-07 correction:
  active scene from our CC80 / pedal B0 50 / PC loads (unknown right after connect), part reads `0C 00 p s`, save writes the
  current tempo into all 3 parts.
- Design: colour-coded pedal cards + LED bypass, block editor (colour header, searchable model list, vector knobs, selector
  buttons, EQ faders, units, "Csúszkák" view), in-place value refresh on re-reads, StatusPage when no pedal, header fits
  860 px. Single stylesheet mg300edit/ui/style.css.
- Screenshots: `gtk4-broadwayd :7 &` then `GDK_BACKEND=broadway BROADWAY_DISPLAY=:7 python3 .dev/shot.py --all` -> .dev/*.png.
- Next: hardware-test the app UI (scene buttons, Save with tempo) on user 3B; `git init` + commit.

## Metronome click in headphones only — DROPPED by user (2026-11-07)
Hardware can't do it (PHONES = OUTPUT mix; drum + USB playback go to both). User declined the laptop-monitoring workaround.

## Scene/tempo correction (2026-11-07)
Active scene = CC80, NOT 15 set (see FACTS.md "CORRECTION 2026-11-07"). Save/scene/tempo hardware-tested on user 3B,
restored byte-exact.

## Next step (2026-11-07)
Final polished build (162 tests) is NOT yet hardware-tested with the CC80 scene logic: pedal was unplugged.
When connected: `timeout 120 python3 .dev/hwtest_save.py` (user 3B / PC 9 only; scene CC80, tempo, save+rename,
restore from research/backup/preset09_3B_LeoGibCleanPush_orig.READONLY.json, byte-exact check). Close any running
editor first (it holds the MIDI port).
Open decision for user: active scene is unknown right after connect (no read-only way found); option = send CC80=0 on connect.

## 2026-11-07: EXP pedal no longer controls volume
PL block switched OFF in all 36 user presets (user request; backup research/backup/all_user_presets_20261107-015843.READONLY.json,
to undo: write that file's raw parts back). saving.py now retries dropped writes (pedal drops ~1 in 3). 163 tests OK.

## Name: OpenTone (2026-11-07)
App ID io.github.barni.OpenTone, launcher `opentone` (symlink to mg300-editor; also ~/.local/bin/opentone),
desktop entry + icon installed in ~/.local/share/{applications,icons}. Source icon: data/io.github.barni.OpenTone.svg.
Python package is still `mg300edit` (internal). Open: EXP/PEDAL target mapping (FACTS.md "PEDAL button").
Icon: own generated icon (data/io.github.barni.OpenTone.svg, data/make_opentone_icon.py) installed locally since 2026-11-07; old NUX photo kept in data/*LOCAL_ONLY* (gitignored).
~/.local/share/icons/hicolor/*/apps/io.github.barni.OpenTone.png. NUX artwork = local/personal use only; if the app is
ever published, switch back to our own data/io.github.barni.OpenTone.svg.

## Takk metronome (2026-11-07, night)
`./takk` (package takkapp/): follows pedal BPM (TAP notification 7E 02 03 -> 03 GET, PC/CC80 re-query, 1 s fallback poll)
and plays a click via pw-cat to the NUX sink, output selector USB 1/2 | 3/4 | all, volume, accent; settings in
~/.config/takk/settings.json. Installed desktop entry + ~/.local/bin/takk. 205 tests OK.
Hardware-tested 2026-11-07 morning: follows TAP; USB 1/2 -> phones+output, USB 3/4 silent (Reamp input); needs sink 100% + Takk 100%.
follows TAP. The NUX PipeWire sink is muted by the user's system settings -> Takk shows a "Feloldás" banner.
Can't run together with OpenTone (exclusive rawmidi port).

## Takk built into OpenTone (2026-11-07)
OpenTone header: "Takk" toggle next to TAP (win.click, Ctrl+K, also in the main menu) with a beat LED
(accent beat amber) + settings popover (Hangerő 100 %, Hangsúly az 1. ütésen, Kimenet USB 1/2 | 3/4 | Mind).
Code: mg300edit/metronome.py (state machine around takkapp.click.ClickGenerator + takkapp.audio.ClickPlayer/
SinkWatcher; shared OUTPUT_CHOICES/FALLBACK_BPM now live in takkapp/click.py), mg300edit/ui/click_control.py,
own icon data/icons/opentone-metronome-symbolic.svg. Settings keys click_volume/click_accent/click_output.
Controller now answers the pedal's TAP notification 7E 02 03 with a 03 GET and emits "tempo" (also on its
own set_tempo); preset/scene loads already re-read 03. While the click plays, the window re-reads 03 every 2 s.
pw-cat/pactl subscribe run only while the toggle is on; sink gone -> stop + toast; muted sink -> toast with
"Feloldás" (pactl set-sink-mute <sink> 0, only on click). Narrow layout (<=1180sp) hides the "Takk" label, the
popover arrow and the copy-scene button -> header fits 860 px (820 px overflowed by ~40 px before, still does).
Screenshot: .dev/opentone_takk.png (shot.py --takk; also _light / _narrow). NOT yet tried on hardware.

## Release v1.0.0 (2026-11-07)
Tag v1.0.0 + GitHub release https://github.com/szabarnabas/OpenTone/releases/tag/v1.0.0 (OpenTone + Takk, 230 tests).
Built-in click in OpenTone not yet confirmed by the user on hardware (standalone Takk was). User levels PATCH LEVEL by ear.
