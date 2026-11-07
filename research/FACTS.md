# NUX MG-300 MKII — verified facts (probed on real hardware 2026-11-06)

- USB 1fc9:8260 "NUX MG-300V2", class-compliant USB MIDI. ALSA rawmidi port "NUX MG-300V2 MIDI 1"
  (here hw:2,0,0 = /dev/snd/midiC2D0, card number can change -> autodetect via /proc/asound/cards or
  /sys/class/sound/midiC*D0/device/../id / name).
- Firmware build string "20260708110000" (Gen2 V3.0.0), model "MG300V2".
- Protocol = MG-30 family (see naikel/NUXFootSwitch docs/Protocol.md):
  SysEx F0 43 58 70 <cmd> <op> ... F7; op 00 get, 01 set, 02 get-response, 03 set-ack.
  Unsupported cmd -> F0 43 58 70 7F 02 00.. F7 (seen for 13, 17, 62, 7E).
- Identity: F0 43 58 00 F7 -> F0 43 58 10 "20260708110000" ... "MG300V2" 00 F7 (also emits C0 <preset>).
- 8-bit data packed 2 bytes -> 3 seven-bit bytes:
    c1 = (d0<<6) | ((d1&0x7E)>>1);  c2 = (d2&0x7F) | ((d1&1)<<7)   (encode is the inverse)
- 03 get tempo -> F0 43 58 70 03 02 00 78 00.. F7  (bpm = b6<<7 | b7)
- 15 get current -> F0 43 58 70 15 02 08 03 58 00.. F7 (b6 = preset 0-based; b7/b8 TBD)
- 0D get block order -> F0 43 58 70 0D 02 05 01 02 03 09 04 06 07 08 0A 00 00 F7 (10 block ids)
- 0C get edit buffer -> 198 bytes: F0 43 58 70 0C 02 <preset> <scene> <189 encoded bytes> F7
  decodes to 126 bytes = one scene, same size as a scene in QuickTone's .mg300MK2patch export.
- 0F get -> 99-byte custom MIDI table (looked like identity map 00 01 02 ...).
- 14 get -> USB routing/levels (43 bytes). 18 looper, 19 drum answer. 09 returns oddly framed data.
- Device drops messages sent < ~40 ms apart (documented for MG-30; assume same).

## Decoded scene sample (preset 8, scene 0, "Surf Rock Clean") — research/scene_a.bin
  00: 41 02 45 09 01 01 43 46 04 09 01 41 00 00 00 03
  10: 19 37 45 00 03 0E 37 41 00 00 00 06 19 23 4B 2D
  20: 41 45 00 00 07 2B 2F 2F 35 38 35 00 00 00 00 00
  30: 00 02 37 19 00 00 03 4E 48 12 00 00 00 04 28 21
  40: 39 D5 00 00 00 00 03 41 23 0D 2D 06 03 02 32 00
  50: 64 00 02 32 32 02 00 64 32 80 00 00 78 05 01 02
  60: 03 09 04 06 07 08 0A 00 00 53 75 72 66 20 52 6F
  70: 63 6B 20 43 6C 65 61 6E 00 00 00 00 00 01
  Known: 0x5C tempo(120), 0x5D-0x66 block order (same as cmd 0D), 0x69.. name (16 bytes, NUL padded).
  Values with 0x40 set likely = model index with block bypassed (MG-30 convention).

## Live control — VERIFIED on hardware 2026-11-06 (CC sweep 12..77, diff of 0C before/after)
- Pedal SENDS CC when user edits on the device: knob -> B0 <cc> <val>, block toggle -> B0 <blk> <model|0x40 if off>,
  preset change -> C0 <n>, CC78 = currently selected block on the pedal UI (does not touch scene),
  F0 43 58 70 7E 02 14 .. F7 = patch-modified notification.
- Pedal ACCEPTS CC on channel 1 (B0) and applies it to the edit buffer immediately (no echo back to sender).
  No 40 ms throttle observed for CCs (60 ms spacing in test, all applied).
- CC N -> decoded scene offset:
    1..11 block switch bytes 0x01..0x0B (CC8 verified = 0x08 RVB; value = model index | 0x40 when bypassed)
    12,13 -> 0x0D,0x0E (WAH slots)      14-17 -> 0x10-0x13 (CMP)    18-23 -> 0x15-0x1A (EFX)
    24-31 -> 0x1C-0x23 (AMP)            32-43 -> 0x25-0x30 (EQ)     44-47 -> 0x32-0x35 (NR)
    48-53 -> 0x37-0x3C (MOD)            54-61 -> 0x3E-0x45 (DLY)    62-65 -> 0x47-0x4A (RVB)
    66-71 -> 0x4C-0x51 (CAB)            72,73 -> 0x53,0x54          74,75,76 -> 0x56,0x57,0x58 (Patch min/max/level)
    77 -> 0x59 stored as 0x80|value (P.L position)
  i.e. layout_draft.json's cc_map is CONFIRMED.
- CAVEAT: CC57 (DLY slot 3, 0x41) is NOT linear: stored byte held 213 originally; CC v gave 0->240,1->241,10->250,20->4,
  30..127 -> v (looks like a signed/offset parameter, values >127 cannot be written by CC). For such params the editor
  must display the stored byte correctly and only write via CC when the target is representable; otherwise use a SysEx
  write path (TBD). Check qt_mg300mk2_display.json for that delay model's param def.
- Revert edits: sending C0 <current preset> reloads the saved preset (discards edit buffer). Verified.
- 15 get: b6 = current preset (0-based).
- Not yet tested: scene CC80, tempo SET (03 op 01), SAVE to preset (MG-30: 0B set + commit) — needs user consent.
- Resolution: 0x41 is NOT a user knob for any DLY model (models use <=4 knobs at 0x3E..0x41? -> per QuickTone config the
  delay models use slots 0-3 with 0-100 ranges; 0x41 holding 213 is a hidden tap/sync value). Editor: only expose params
  defined in layout models[].params, never write hidden slots.

## Presets, scenes, SAVE — VERIFIED on hardware 2026-11-06 (tested only on user 3B = PC 9, restored byte-exact)
- Preset naming (confirmed by user on display): 4 slots per bank A-D. PC 0-35 user 1A..9D, PC 36-71 factory 1A..9D.
  name(i) = f"{i%36//4+1}{'ABCD'[i%4]}". User 3B = PC 9 "LeoGib CleanPush". PC 72-127: empty/garbage, hide.
- Each preset has 3 scenes (parts 0-2) that really differ (e.g. scene 1/2 change EFX/AMP values).
- Read edit buffer of a given scene: F0 43 58 70 0C 00 <preset> <scene> F7 ; plain `0C 00` = CURRENT scene
  (reply byte 7 = current scene index).
- Read stored: F0 43 58 70 0B 00 <preset> <scene> F7 -> 198-byte 0B 02 reply.
- Switch scene: F0 43 58 70 15 01 <preset> <scene> 00 00 00 00 00 00 F7 -> reply 15 02 <preset> <scene> ..,
  then pedal emits B0 4E <block?> and B0 50 <scene> (CC80 = scene, OUTGOING notification only).
  Sending CC80/CC79 to the pedal does NOT switch scenes.
- SAVE (QuickTone sequence, works):
    for part in 0..2: F0 43 58 70 0B 01 <target> <part> <189 enc bytes> F7  -> ack F0 43 58 70 0B 03 <target> <part> 01 00.. F7
    commit:          F0 43 58 70 07 01 <target> <target> 00 00 00 00 00 00 F7 -> ack F0 43 58 70 07 03 <t> <t> 00.. F7
  Readback via 0B get matches payload exactly. Writing to the currently loaded preset reloads the edit buffer.
  Save-current-edits = read 0C <cur> <s> for s=0..2, write as 0B to target, commit, verify via 0B readback.
- Rename = change bytes 0x69..0x78 (16 bytes ASCII, NUL padded) in all 3 parts and save.
- Backups of tested presets: research/backup/*.READONLY.json
- TEMPO SET: F0 43 58 70 03 01 <hi> <lo> <x> 00 00 00 00 00 F7 -> reply 03 02 echo. bpm=(hi<<7)|lo. Global (scene bytes
  0x5B/0x5C unchanged). Byte 8 <x> is stored and echoed by later GETs (meaning unknown) -> always send back the value
  from the last 03 GET reply (normally 00). SIDE EFFECT: a tempo set jumps the pedal to scene 0 -> app must re-send
  15 set <preset> <previous scene> right after.
- BLOCK ORDER SET: F0 43 58 70 0D 01 <10 block ids> 00 00 F7 -> reply 0D 02 <new order>; edit buffer bytes 0x5D-0x66
  follow. Non-persistent until saved. (tested swap MOD/DLY, reverted via PC reload)
- PC <current preset> reload also resets the current scene (landed on scene 1 once) -> after revert, re-send 15 set
  with the scene the user was on.

## Presets + stored-scene read — VERIFIED on hardware 2026-11-06
- 72 presets: PC 0-35 = user/custom bank, PC 36-71 = factory bank.
  Display naming (verified on the pedal display): each group = 9 banks x A/B/C/D,
  index i within the group -> f"{i//4+1}{'ABCD'[i%4]}" (factory index = pc-36), i.e. 1A..9D.
  User 3B = PC 9 ("LeoGib CleanPush"), factory 3B = PC 45.
  PC 72-127 return empty/garbage names -> never shown (mg300edit/presets.py).
- Read a STORED preset scene (read-only, 0B get):
    send  F0 43 58 70 0B 00 <preset> <scene> F7
    reply F0 43 58 70 0B 02 <preset> <scene> <189 encoded bytes> F7   (198 bytes)
  Same 2->3 encoding as 0C; decodes to one 126-byte scene, name at 0x69 (16 bytes, NUL padded).
  Scenes 0..2 return different data -> a preset holds 3 scenes.
  The device answers in ~2 ms; still keep >= 40 ms between SysEx (the writer does).
- 0B SET / save-to-pedal: NOT known yet (being reverse engineered). The editor only has a stub
  (Controller.save_preset raises NotImplementedError; "Mentés" button disabled).

## QuickTone .mg300MK2patch (per hemanthsuryya/mg300-forge-II app/preset_format.py + its template)
- 8654 bytes: header 11 00 00 00, then three 126-byte decoded scenes at file offsets 4, 130, 256
  (byte-identical layout to the 0C/0B decoded scene: model bytes at 0x00.., name at 0x69; each scene
  ends in 00 00 00 01 like the 0C dump), then ~8 KB zeros (presumably embedded-IR area). No checksum
  observed by that project. The editor imports only the three scenes (mg300edit/fileformat.py).

## CORRECTION 2026-11-07 — scenes & tempo (verified on user 3B = PC 9, user confirmed via pedal display)
- ACTIVE SCENE SWITCH = CC80 (B0 50 <0|1|2>). Verified: after CC80=n, CC param edits land in edit-buffer part n.
  Pedal also emits B0 50 <scene> when the scene changes on the device. CC79 does nothing.
- `15 01 <p> <s>` does NOT switch the active scene (display stayed on Scene 1). It only sets which part a plain
  `0C 00` (no args) returns = an editor "view" index. 0C reply byte 7 = that view index, NOT the active scene.
  -> Never use 15 set / 0C byte 7 as the active scene. Track active scene from CC80 (ours and the pedal's).
- CC param edits always go to the ACTIVE scene's part. Read parts explicitly with `0C 00 <p> <s>`.
- PC reload (revert) resets the active scene to 0 -> re-send CC80 <previous scene>.
- TEMPO: `03 01 hi lo x` sets live tempo and the tempo bytes 0x5B/0x5C of the active scene's part (seen: part 0 while
  scene 0 active). A following `15 set` reloads/reverts it (so don't send 15 after tempo). Tempo is stored PER SCENE
  in the preset (0x5B/0x5C) -> to persist, save; when saving set 0x5B/0x5C in all 3 parts to the chosen tempo
  (unless user wants per-scene tempo).
- Earlier entries above claiming "15 set = switch scene" and "CC80 does not switch scenes" are WRONG.

## EXP pedal = volume via PL block (verified 2026-11-07)
- The built-in expression pedal drives the PL ("PATCH LEVEL"/volume) block between PATCH MIN (0x56) and MAX (0x57).
  All user presets had PL on with MIN=0 -> heel position = silence. MIN=MAX=100 still left it quieter at heel;
  PL block OFF (switch byte 0x0A |= 0x40, CC10) = pedal has no effect on volume (user confirmed by ear).
- 2026-11-07: PL turned OFF in all 36 user presets at the user's request (35 changed, 7C was already off).
  Verified: only byte 0x0A differs from backup research/backup/all_user_presets_20261107-015843.READONLY.json.
- Pedal sometimes silently drops a 0B write (no ack; ~1 in 3 presets, usually the 2nd part). Re-sending after
  ~0.4 s always worked -> saving.py retries writes/commit/reads (RETRY_AFTER 0.5 s, MAX_RETRIES 3).

## PEDAL button / EXP target (observed 2026-11-07, preset 3A)
- Pressing the PEDAL button cycles scene byte 0x7D (all 3 parts) 0->1->...->6->0 and the pedal sends CC79 (B0 4F n).
  7 states, likely = user's list WAH/EFX/AMP/MOD/DLY/RVB/none (index->target mapping NOT confirmed). Display showed "WAH".
- CC79=n sent to the pedal sets 0x7D (accepted). Moving the EXP pedal sends NO MIDI and changes no 0C/14 bytes.
- With 0x7D=2 on 3A (EFX/MOD/DLY bypassed there) moving EXP changed nothing audible. Unknown: which parameter each
  target controls, whether the target block must be on, and what 0x79-0x7C (00 00 00 00) mean (possibly EXP ranges).
