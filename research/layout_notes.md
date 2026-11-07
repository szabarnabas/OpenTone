# MG-300 MKII scene layout: notes (draft, 2026-11-06)

Offsets below are into the decoded 126-byte scene (the 0C/0B payload after 8-to-7-bit unpacking). Hex is `0x..`, decimal is in brackets.
Machine-readable version: `layout_draft.json`. CC map: `cc_map_draft.json`.

## Main result

The MG-300 MKII scene uses the **MG-30 scene layout byte for byte up to 0x51 [81]**. Only the tail changes: there is no S/R block, the volume/PL area is one byte shorter, and the scene stops after 5 bytes past the name.
Each block has a fixed param area of `[count byte][N value slots]`, and each model uses the first slots, so the hypothesis holds.
A param offset depends only on the block and the knob index. The model only changes the names, ranges and defaults.
Knob index = QuickTone `midiCC` in the per-model UI config = Custom-MIDI knob number.

| Offset | Content |
|---|---|
| 0x00-0x0B | block switch bytes, index = block id = switch CC: 0 WAH, 1 CMP, 2 EFX, 3 AMP, 4 EQ, 5 NR, 6 MOD, 7 DLY, 8 RVB, 9 CAB/IR, 10 PL, 11 ? |
| 0x0C / 0x0D-0x0E | WAH count / 2 slots |
| 0x0F / 0x10-0x13 | CMP count / 4 slots |
| 0x14 / 0x15-0x1A | EFX count / 6 slots |
| 0x1B / 0x1C-0x23 | AMP count / 8 slots |
| 0x24 / 0x25-0x30 | EQ count / 12 slots |
| 0x31 / 0x32-0x35 | NR count / 4 slots |
| 0x36 / 0x37-0x3C | MOD count / 6 slots |
| 0x3D / 0x3E-0x45 | DLY count / 8 slots |
| 0x46 / 0x47-0x4A | RVB count / 4 slots |
| 0x4B / 0x4C-0x51 | CAB count / 6 slots: MIC, MIC POS, LEVEL, LOW CUT, HIGH CUT, (unused) |
| 0x52 / 0x53-0x54 | count=2 / 50,50: probably vestigial Send/Return (CC72/73) |
| 0x55 / 0x56-0x57 | count=2 / PATCH MIN (0), PATCH MAX (100) |
| 0x58 | PATCH LEVEL (50 = 0 dB?) |
| 0x59 | 0x80: P.L Position (CC77) or a flag? |
| 0x5A | 00, unknown |
| 0x5B-0x5C | tempo: `(b[0x5B]<<7) \| b[0x5C]` |
| 0x5D-0x66 (+0x67-0x68 pad) | block order: 10 ids, 2 zero pad bytes (the MG-30 has 12) |
| 0x69-0x78 | name, 16 bytes |
| 0x79-0x7D | `00 00 00 00 01` unknown. On the MG-30, CTRL/EXP assignments follow the name. |

**Model byte:** bits 0-4/0-5 hold the 1-based QuickTone menu position, 0x40 = bypassed, 0x20 = alternate block type. The CMP slot can host an EFX, EFX a MOD, and RVB a DLY ("Hold: CMP<-->EFX" etc. in QuickTone). The alternate model lists are in the JSON.

**Count byte:** it usually equals the knob count (T SCREAMER 3, NR 2, ST.CHORUS 3, PAN 4, CAB 6, RED COMP 2), but it does not always match: HALL stores 2, SPRING 3, CLASS A30 6, ROSE COMP 3.
Treat it as opaque and preserve it. Slots the current model does not use keep stale values from earlier models (CMP slot2=69, AMP slot5=69, RVB slots 2-3). Preserve those too.

## Evidence and confidence

- **verified (hardware, FACTS.md):** name 0x69/16, block order 0x5D (= cmd 0D), tempo low byte 0x5C=120.
- **verified (file diffs by hemanthsuryya/mg300-forge-II):** they exported single-knob changes from QuickTone and diffed them. Their file offsets are wire offsets **+4**.
  The `.mg300MK2patch` file has a 4-byte header (`11 00 00 00`), then 3 x 126-byte scenes at file 0x04, 0x82 and 0x100. The code puts scene bases at 0, 0x7E and 0xFC and adds the +4 to every offset constant, so it is self-consistent.
  This was checked on their template file: the name lands at file 0x6D = wire 0x69, and `00 00 00 00 01` at the scene end matches our sample.
  Verified this way: model bytes CMP 0x01, EFX 0x02, AMP 0x03, MOD 0x06, DLY 0x07, RVB 0x08, IR 0x09 (the menu index 1-30 for AMP, plus the bypass bit); AMP slots 0-5 at 0x1C-0x21 (FIREMAN HBE: GAIN, PRES, LEVEL(=“Master”), BASS, MIDDLE, TREB); RVB slot 3 at 0x4A (HALL LEVEL).
- **likely: everything else in the block areas.** The sources are:
  - the MG-30 decoded-scene table in athedsx/open-mg30 docs/protocol.md. Its offsets 0-81 match our sample's structure exactly, and the count bytes fall where predicted.
  - QuickTone's embedded `MG300MK2_DisplayConfig.json`, extracted from QuickTone.exe at file offset 0x69f8db0. Per model it gives the label, knob index (`midiCC`), min, max and default. On/off indicators use midiCC 50+block id (51 CMP … 58 RVB, 60 PL), which confirms the block ids.
  - the MG-300V2 dropdown lists in QuickTone (header string "NUX MG-300V2"), which match the forge catalog.
  - The sample makes sense under this mapping:
    - AMP 9 = CLASS A30 with IR 9 = BLUE 212. The pedal auto-matches the cab to the amp, which supports the indexes.
    - EFX 5 T SCREAMER, count 3.
    - RVB 4 SPRING ("Surf Rock").
    - CAB MIC=3 (SM57), POS=2.
- **guess:**
  - WAH. Firmware V3.0.0 release notes add "WAH (4 models), hold PEDAL button", but QuickTone 2.6.7.16 has no MG-300V2 wah list, so the names are the MG-30 ones.
  - NAM model numbers (fw adds 10 NAM slots; probably 31-40).
  - CAB COLLECTION/USER values.
  - 0x0B, 0x52-0x54, 0x59-0x5B, 0x79-0x7D.
  - The scales (dB/Hz formulas copied from MG-400 notes).
  - Toggle values: QuickTone uses 0/100 for switches and 0/1 for BRIGHT.
  - DLY hidden slot 3 (PAN shows 0xD5; the MG-30 has similar subdiv/flag bytes, so mask with 0x0F).
- Firmware strings had no model or param names (the display uses bitmaps). The manual PDF is outlined text, so its only usable content was the block list "NG, CMP, EFX, AMP, IR, EQ, MOD, DLY, RVB, P.L".

## Hardware diff tests to run (change one thing on pedal → re-read 0C → diff)

1. EQ on/off and 6-EQ↔10-EQ → expect 0x04 toggles 0x40 / 1↔2. Also check whether count byte 0x24 changes (7→12?).
2. NR on/off, SENS → 0x05, 0x32.
3. CMP SUSTAIN / LEVEL (ROSE) → 0x10 / 0x11. Change the CMP model and watch count byte 0x0F.
4. EFX knob 1-3 → 0x15-0x17. Hold-swap CMP→EFX: expect 0x01 |= 0x20.
5. MOD knobs → 0x37-0x39. Swap EFX→MOD: 0x02 |= 0x20.
6. DLY TIME/REPEAT/LEVEL → 0x3E-0x40. Change the tap tempo/subdiv and watch 0x41 (0xD5) and 0x5B/0x5C.
7. RVB knobs → 0x47-0x48. Swap RVB→DLY: 0x08 |= 0x20.
8. CAB MIC, POSITION, LEVEL, LOW/HIGH CUT → 0x4C-0x50. Select USER IR and COLLECTION IR → 0x09 values.
9. AMP: select a NAM slot → 0x03 value. Toggle BRIGHT on JAZZ CLEAN → 0x1D (0/1?).
10. PL block: on/off → 0x0A. Patch level, min, max → 0x58, 0x56, 0x57. Move PL in the chain → 0x5D-0x66 and 0x59.
11. WAH: hold PEDAL, pick a model, toggle → 0x00, 0x0C-0x0E (and maybe 0x0B).
12. Tempo 120→121 and →250 to confirm the 0x5B high byte.
13. Change the EXP/footswitch assignment if possible → 0x79-0x7D.
14. CC check: send CC80=1 (scene 2) vs CC79; CC78 (current block); CC3 value 0x49 (AMP 9 off) → confirms the cc_map shift caused by P.L Position.

## Sources
QuickTone.exe 2.6.7.16 (DisplayConfig JSON, MG-300V2 dropdown lists, Custom-MIDI string table next to `UI_MIDI_Image_Title_MG300MK2_MG400`); MG-300MKII_V3.0.0.bin release notes; athedsx/open-mg30 docs/protocol.md + src/constants.ts; voronkovd/nux-mg30-mcp docs/PROTOCOL.md; naikel/NUXFootSwitch docs/ControlChanges.md; willianszwy/mg-400 data/mg400.json + SKILL.md + scripts/mg400.py; hemanthsuryya/mg300-forge-II app/preset_format.py, tests, catalog, template.
Extracted helper data (copied here): `qt_mg300mk2_display.json` (QuickTone per-model UI config), `qt_mg300v2_model_lists.txt` (QuickTone dropdown lists), `gen_layout.py` (regenerates layout_draft.json: `python3 -I gen_layout.py qt_mg300mk2_display.json qt_mg300v2_model_lists.txt scene_a.bin layout_draft.json`).
