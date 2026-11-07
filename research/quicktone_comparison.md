# QuickTone 2.6.7.16 (MG-300V2 mód) vs. mg300-editor — összehasonlítás és hiánylista

Készült: 2026-11-06. Források: a `QuickTone.exe` statikus vizsgálata (C++ RTTI osztály- és metódusnevek, beágyazott nyelvi fájl, JUCE BinaryData képek; a programot nem futtattuk), a `research/` mappa (FACTS.md, layout_notes.md, qt_*.json/txt), mások MG-30 protokoll-leírásai (naikel, athedsx, voron), a saját kódunk (`mg300edit/`) és a `ui_draft.png` képernyőkép. A webes forrásokat a végén soroljuk fel.

Jelölés: **van** / **részben** / **nincs**. RE = hardveres visszafejtés kell-e hozzá.

## 1. Mit tud a QuickTone az MG-300 MKII-vel (bináris alapján)

Az MG-300 MKII-hez tartozó osztályok:
`NuxMidiTransmissionMG300MK2`, `UserDataMG300MK2`, `NuxInstrumentPanelMG300MK2`, `NuxSidePanel(+Cell)MG300MK2`, `NuxPresetSave/SaveAs/SaveSelect/Import/Export/ChoosePanelMG300MK2`, `NuxRestorePanelMG300MK2`, `NuxDrumLoopPanelMG300MK2`, `NuxPedalSettingPanelMG300MK2`, `NuxMidiSettingPanel(+Cell)MG300MK2`, `NuxSettingPanelMG300MK2`, `NuxPatchLevelComponentMG300MK2`, `NuxCabinetParameterPanelMG300MK2`, `NuxCustomCabinetCollection/Parameter/Export/PasteToPanelMG300MK2`, `NuxImagePanelMG300MK2`, `NuxPopupMenuMG300MK2`.

A `NuxMidiTransmissionMG300MK2` metódusai (ezek a protokoll-műveletek):
`sendLoadPresetCheck`, `sendSavePresetCheck`, `setCurrentPresetCheck`, `getCurrentPresetSavePreset`, `getCurrentPresetResetPreset`, `exportPresetLogic`, `exportAllPresetLogic`, `getBPMCheck`, `getSystemSetCheck`, `setDeviceStateCheck`, `getModuleLinkCheck`, `loadIRCurve_Check`, `deleteIRCheck`, `deleteCollectionIRCheck`, `pasteToIRCheck`, `get/setIRCrcAndName`, `exportIRWavFile`, `loadBlackBox_Check`, `getBlackBoxInfo_Check`, `getBlackBoxGen_Check`, `loadBootUpPicture_Check`, `setQuickToneVersionCheck`.

A `UserDataMG300MK2` metódusai: `setCurrentPresetMidiCCValue` (ebből úgy tűnik, a QuickTone is CC-vel szerkeszt élőben, ahogy mi), `setCurrentSceneIndex`, `syncPresetGroup(SYNC_SCENE_TYPE)`, `setCurrentPresetBpm`, `setCurrentPresetCMPChangeEFX`, `setCurrentPresetEFXChangeMOD`, `setCurrentPresetRVBChangeDLY`.

Fájlok: `.mg300MK2patch` (egy preset: 4 bájtos fejléc `11 00 00 00` + 3 × 126 bájtos scene) és `MG300MK2AllPatch.mg300MK2patch` (az összes preset). IR-hez `.wav`, NAM-hoz `.nam`.

Nincs benne: firmware-frissítés (ezt a firmware-csomagban lévő külön `NUX Device Updater.exe` végzi; a QuickTone csak a verziót és a „FW Release Note”-ot mutatja), felhős hangszín-megosztás vagy fiók (a binárisban egyetlen NUX URL sincs), és MG-300 MKII-hez nincs tuner-panel (a tuner-szövegek más termékekhez tartoznak).

## 2. Funkció-mátrix

| Funkció | QuickTone | mi | Megjegyzés / protokoll |
|---|---|---|---|
| Eszközfelismerés, hotplug, verzió | van (`setQuickToneVersionCheck` → cmd `62`) | **van** | Az identity (`F0 43 58 00`) működik, a hotplug-loop is. A `62`-re a pedál „unsupported”-ot felel nálunk, ezért ezt kihagyhatjuk. |
| Preset váltás | van | **van** | PC `C0 n`, ←/→/PgUp/PgDn |
| Presetlista nevekkel (oldalsáv, User/Factory fül, módosított-állapot ikon, patch másolás/csere) | van (factory szürke, user fehér; `NuxSidePanel`, `Side_Button_User/Factory`, `Preset_Icon_User/Factory`, `Preset_Image_Edit_Preset_State`) | **nincs** (csak spinbox + az aktuális név) | 72 preset: user 1A..9D (PC 0-35) + factory 1A..9D (PC 36-71), bankonként 4 slot (A-D). A tárolt presetek olvasása: `0B 00 <p> <scene>` (read-only, ismert). **RE: nem kell.** |
| Élő szerkesztés: blokk be/ki, modell, paraméterek | van (knobok a képen) | **van** | CC 1-77, a hardveren ellenőrizve. A QuickTone is CC-t használ (`setCurrentPresetMidiCCValue`). |
| CMP↔EFX, EFX↔MOD, RVB↔DLY slotcsere | van („Hold: CMP<-->EFX” stb.) | **részben** | A layout ismeri a 0x20-as alt bitet, de a UI nem mutatja külön blokktípusként. A bitet hardveren ellenőrizni kell (kis RE). |
| Mentés / Save As slotválasztással | van (`sendSavePresetCheck`, „Please select a slot…”) | **nincs** | MG-30-on így megy: 3 × `0B op01 <p> <part>` + commit `07 01 <p> <p>`. MG-300 MKII-n még nem teszteltük. **RE: igen, közepes, és destruktív** (egy szabad user sloton kell kipróbálni). |
| Átnevezés | van (Save dialógus „Name:”) | **nincs** | A név a 0x69-0x78 bájtokon van, mentéssel együtt írható. Az MG-30-on a `12` „rename”, ezt próbálni kell. RE: kicsi (a mentésre épül). |
| Szerkesztés eldobása / preset reset | van (`getCurrentPresetResetPreset`) | **van** | Ctrl+Z → PC az aktuális presetre (ellenőrizve) |
| Módosított-állapot jelzés | van (ikon) | **nincs** | A pedál `7E 02 14` értesítést küld, ez ismert. **RE: nem kell.** |
| Scene-ek (3/preset): 1/2/3 gomb, Copy Scene, Sticky/Pro Scene mód | van (`Scene_Button_Change_1-3`, `Scene_Button_Copy`, „Sticky Mode”) | **részben** (a scene indexét kiolvassuk a `0C`-ből, váltani nem tudunk) | MG-30-on CC80 = scene 0/1/2, MG-300 MKII-n még nem teszteltük. A Copy Scene `0B` írást igényel. RE: kicsi (CC80), a másoláshoz közepes. |
| Blokksorrend megjelenítése | van | **van** | `0D get` / 0x5D-0x66 |
| Blokksorrend drag&drop | van (a NUX termékoldala szerint szabadon átrendezhető; `InstrumentPanel` mouseDown/Up) | **nincs** | `0D op01` set, nem teszteltük. RE: közepes. |
| Tempo / BPM | van (`getBPMCheck`, `setCurrentPresetBpm`, TAP gomb a Drum panelen) | **részben** | A spinbox csak lokális: a `cc_map`-ben nincs `tempo`, a `03 op01` nem tesztelt. Tap tempo nincs. RE: kicsi. |
| Patch Level (MIN/MAX/LEVEL ±12 dB, Pre/Post) | van (`PatchLevel_MG300MK2_Bg`, `PatchLevel_Toggle_Pre/Post`) | **részben** | A PL slider van. A Pre/Post (CC77, 0x59) nincs a UI-ban. A dB-skála csak feltételezés. |
| Cab paraméterek (mic típus, CENTER/MIDDLE/EDGE pozíció, level, low/high cut) | van (mikrofonképek) | **van** (sliderekkel) | — |
| User IR import (WAV), törlés, export WAV-ba, név+CRC, IR Collection, „Paste IR to Other Presets” | van (24 user slot, 512 minta/IR, Level/Low/High cut) | **nincs** | cmd `09` (IR név), `10` (IR adat), CRC. **RE: igen, nagy.** A slotszámot, a formátumot és a CRC-t ki kell deríteni. |
| NAM („BlackBox”) betöltés `.nam`-ból, NAM amp (GAIN/BASS/MIDDLE/TREB/MASTER) | van (`loadBlackBox_Check`, `_Icon_amp_nam_loader`) | **nincs** | A fw V3.0.0 10 NAM slotot ad. A NAM modell-indexek csak feltételezések. **RE: igen, nagy** (fájlátvitel). |
| Drum gép (DRUM, TEMPO, VOL, BASS/MIDDLE/TREBLE) | van (`NuxDrumLoopPanel`) | **nincs** | cmd `19` (a get válaszol). RE: közepes. |
| Looper (REC/PLAY/DUB/UNDO/REDO/STOP/CLEAR, LEVEL, Auto Record, Mono/Stereo) | van | **nincs** | cmd `18` (a get válaszol). RE: közepes. |
| EXP / pedálbeállítás (WAH modell: CLYDE/CRY BB/V847/HORSE WAH; FS mód: PRESET▼▲ / SCENE▼▲ / SCENE▼+PRESET▲) | van (`UI_Pedal_Image_MG300MK2_1-4`) | **nincs** | A `13` itt nem támogatott, ezért valószínűleg a system set része (`getSystemSetCheck`). RE: közepes. |
| USB audio: Recording/Playback Level, Routing (Normal / Reamp / Dry Out + USB Loopback; Cab Sim, Dry/Wet) | van (`Setting_Image2` = 3 routing-ábra; a manual szerint a routing QuickTone-ban állítható) | **nincs** | cmd `14` (43 bájtos válasz, ismert get). Linuxon különösen hasznos. RE: kicsi-közepes. |
| Globális beállítások: Global EQ (GEN2), kimeneti mód (Studio Direct / Combo/Stack Front/Rear), input trim, phones/output szint, Aux/Mic szint | a pedálon biztosan; QuickTone-ban valószínű (`getSystemSetCheck`, `NuxSettingPanelMG300MK2`) | **nincs** | System-set parancs ismeretlen. RE: közepes |
| Custom MIDI (CC-tábla, MIDI List, export) | van (`NuxMidiSettingPanel`) | **nincs** (a `0F` olvasható, 99 bájt) | Írás: RE közepes. A CC-térképünk már megvan, ezt meg tudjuk jeleníteni. |
| Boot-up kép (800×480 PNG/JPEG, kivágás) | valószínűleg van (`loadBootUpPicture_Check`, `NuxImagePanelMG300MK2`, `button_image`) | **nincs** | Képátvitel. **RE: nagy.** Alacsony érték. |
| Gyári visszaállítás (összes preset) | van (`NuxRestorePanelMG300MK2`, „…Please restart the device”) | **nincs** | A `17` nálunk nem támogatott, az útvonal ismeretlen. RE: nagy és kockázatos. |
| Preset export/import (`.mg300MK2patch`) | van | **nincs** | A formátum ismert (forge, layout_notes): az export **RE nélkül megy** (3 × `0B` olvasás), az importhoz a mentés kell. |
| Teljes backup (All Patch) | van (`exportAllPresetLogic`) | **nincs** | 72 × 3 `0B` olvasás, read-only, **RE nélkül, most is megoldható.** |
| Firmware-frissítés | **nincs** (külön Updater) | nincs | A firmware-verziót kijelezzük. |
| Felhő / tone sharing | **nincs** | nincs | — |
| Knob vezérlési mód (Straight/Round), dupla kattintás = alapérték | van („Knob Control”, „Double Click to Recall”) | **nincs** | A layoutban vannak defaultok: egyszerű UI-feladat. |
| Többnyelvű UI | 9 nyelv | csak angol | Később gettext, magyarral. |
| MIDI monitor, nyers SysEx küldés | nincs | **van** | Ez a mi előnyünk (RE-barát). |
| Hardver nélküli (fake) mód | nincs | **van** | Fejlesztéshez és demóhoz. |
| Natív Linux, gyors indulás, kicsi | nincs (Win/mac, kb. 190 MB exe) | **van** | GTK4/libadwaita. |

## 3. Design-összehasonlítás

### A QuickTone kinézete (a beágyazott képek alapján)

- **Skeuomorf „próbaterem”.** Az ablak háttere egy terem: polc, falburkolat, padló, a sarokban kombók és gitár (`Image_InstrumentPanel_Bg_MG300MK2.jpg`, 1468×1470). A kiválasztott blokk fotórealisztikus képként jelenik meg: az effekt egy taposó, az amp egy erősítő-előlap.
- **Blokk-szerkesztő = valódi pedál/amp kép, rajta knobokkal.** Például: `EFX_T_Scream.png` (zöld TS taposó, DRIVE/TONE/LEVEL), `Amp_Class_A30_Panel.png` (1338×264-es amp-előlap, a knobok helye pontokkal jelölve). A knobok 132 képkockás forgó filmcsíkok (`Knob_36.png`). Az EQ-hoz függőleges faderek tartoznak (`_Fader_*`).
- **A lánc ikonsorként jelenik meg** (`_Icon_*`, kb. 85×129-es mini taposók és amp-ikonok). A blokkok szöveges címkéi: NG, CMP, EFX, AMP, CAB, EQ, MOD, DLY, RVB, PL, WAH.
- **Bal oldalt a presetlista** User/Factory váltóval. A kiválasztás színe arany (`#e1bd1c`, `Side_Button_User_c.png`).
- **Felül egy sötét eszköztár** (`#1b2328`). Benne: kapcsolat-LED, presetnév-mező, mastervolume-sáv, valamint Save / Import / Export / Drum / Pedal / Image / Settings / Info ikonok (72×66). Mellette a Scene 1/2/3 + Copy gombok.
- **A dialógusok** sötétszürke kártyák (`#3a3a3a`, `PatchLevel_MG300MK2_Bg.png`) nagy, lekerekített gombokkal (Apply/Cancel/Default/Delete/Export/Import/PasteTo/Restore/Save, 248×66). A beállítások táblázatai világosak (MIDI CC-tábla, `UI_MIDI_Image_CC_1.png`). Ez stílustörés a sötét főablakhoz képest.
- **A folyamatokat illusztrációk magyarázzák:** USB-routing ábrák (`Setting_Image2.png`), pedál- és footswitch-mód kártyák (`UI_Pedal_Image_MG300MK2_3.png` / `_4.png`), eszközválasztó vonalrajz (`Image_Launch_Image_2.png`).

**A legjellemzőbb kinyert képek** (`/tmp/claude-1000/-home-barni/2675b00d-1589-4aed-95d7-82bf70d8182f/scratchpad/qt_img/`):

| Erőforrás | Kinyert fájl |
|---|---|
| Image_InstrumentPanel_Bg_MG300MK2.jpg (háttér) | `jpg_00b056258_1468x1470.jpg` |
| EFX_T_Scream.png (pedálszerkesztő) | `png_004173898_573x924.png` |
| Amp_Class_A30_Panel.png (amp-előlap) | `png_0016eec78_1338x264.png` |
| _Icon_Amp_Class_A30.png (lánc-ikon) | `png_0052b1fe8_262x217.png` |
| PatchLevel_MG300MK2_Bg.png | `png_005654b98_1138x610.png` |
| UI_Pedal_Image_MG300MK2_3/_4.png (FS/WAH) | `png_00a736268_360x293.png`, `png_00a738438_569x293.png` |
| Setting_Image2.png (USB routing) | `png_00a5dc810_648x354.png` |
| Knob_36.png (knob-filmcsík) | `png_008d1a5f0_1920x1600.png` |
| Image_Launch_Image_2.png (eszközválasztó) | `png_00b286f98_1164x792.png` |

A név és a fájl párosítása sorrend szerinti: a resource-névlista és a képek offset-sorrendje megegyezik, és a jpg-pozíciókon ellenőriztük. A teljes lista: `scratchpad/cmp/zip.txt`.

### A mienk (ui_draft.png)

- Felül egy HeaderBar: státusz-pont, preset ‹ szám › név, BPM, refresh, monitor, menü.
- Alatta a lánc kártyagombokként: blokk-rövidítés, modellnév és egy Gtk.Switch.
- Középen egy libadwaita PreferencesGroup: modell-legördülő, és paraméterenként egy sor vízszintes sliderrel. Debug-szöveg is látszik („offset 28 (0x1C)”, „model byte @ 3”).
- Lent a MIDI-monitor panel. Sötét téma.

### Mit érdemes átvenni (ötletként, NUX-grafika nélkül)

1. **Presetlista oldalsáv** (Adw.NavigationSplitView): User / Factory szekció, „1A … 9D” címkék és nevek, a módosított preset jelölése, keresés. Ez a legnagyobb UX-hiány.
2. **Színkód blokktípusonként** a láncban és a szerkesztő fejlécében: például drive = narancs, amp = piros, mod = lila, dly = kék, rvb = türkiz, dyn/NR = szürke, EQ = zöld, CAB = barna, PL = semleges. Bypass esetén deszaturált. A saját palettánk legyen, ne a NUX pedálszínei.
3. **Knob a slider helyett** (vagy választható): egy saját rajzolt, vektoros arc-knob (Gtk.DrawingArea) értékívvel. Kompaktabb, mint egy 280 px-es slider, és egy blokk 2-8 paramétere egy sorban, „pedálfelületként” elfér. A slider maradjon a billentyűzetes és akadálymentes használatra. Dupla kattintás = default (ezt a QuickTone is tudja).
4. **Pedálszerű blokk-kártya a láncban:** kisebb, ikonnal vagy betűjellel, színsávval. A ki/be kapcsolás LED-szerű pötty vagy kattintás legyen a Gtk.Switch helyett, ami túl nagy. Drag&drop sorrendhez előkészítve.
5. **Scene 1/2/3 szegmentált gomb + „Copy scene”** a fejlécben, a preset mellett.
6. **Eszköztár-műveletek egy helyen:** Save, Save As, Import, Export, Backup all (Adw split-button vagy menü).
7. **Magyarázó ábrák** a beállításokhoz (USB-routing módok): saját, egyszerű SVG-ként.
8. **Egységes értékkijelzés**: dB, Hz, ms, és a választós paramétereknél nevek (pl. mic: SM57) a nyers 0-100 helyett.

### Amiben jobbak vagyunk / maradjunk

- A natív, gyors, kis méretű, téma- és HiDPI-követő UI a 190 MB-os, fix bitképes, skálázási gondokkal küzdő skeuomorf felülettel szemben.
- Nagyobb információsűrűség és pontos számértékek: nem kell egy fotón keresni, hol a knob.
- Billentyűzetes navigáció, Ctrl+Z revert, F5 refresh.
- MIDI-monitor, nyers SysEx és fake backend: fejlesztői és RE-eszköz, ilyen a QuickTone-ban nincs.
- No-echo guard és coalescing writer: kevesebb ugrálás és visszhang.
- A debug-feliratokat (offset, model byte) viszont egy „Developer mode” kapcsoló mögé kell tenni. Normál módban zavaróak.

## 4. Priorizált TODO

| Prio | Feladat | RE kell? |
|---|---|---|
| **P1** | Presetlista oldalsáv: 72 preset nevének beolvasása `0B 00 <p> 0`-val, háttérben, 40 ms-os ütemezéssel. User/Factory, 1A-9D (4 slot/bank), kattintásra PC. | Nem |
| **P1** | Teljes backup és egy preset exportja `.mg300MK2patch`-be (`11 00 00 00` + 3 × 126 B). Csak olvasás. | Nem |
| **P1** | Bug: `layout.json`-ból hiányzik a `preset_count` (72), ezért a spinbox 128-ig megy. | Nem |
| **P1** | Tempo írás: a spinbox most csak lokális. `03 op01` teszt, vagy ha nem megy, tiltsuk le. Tap tempo gomb. | Kicsi |
| **P1** | Módosított-állapot jelzés (`7E 02 14`) + figyelmeztetés presetváltáskor. | Nem |
| **P1** | Mentés (Save / Save As slotválasztóval + átnevezés): `0B op01` × 3 + `07` commit az MG-30 minta szerint, egy szabad user sloton tesztelve, visszaolvasással ellenőrizve. | **Igen**, közepes (destruktív, felhasználói hozzájárulással) |
| **P1** | Design: színkódolt, kompaktabb lánckártyák, LED-szerű bypass, debug-feliratok Developer mode mögé. | Nem |
| **P2** | Scene-váltó (1/2/3), CC80 teszt. Az aktuális scene kijelzése a `0C`-ből. Copy scene (mentés-útvonalra épül). | Kicsi / közepes |
| **P2** | Knob widget (saját rajzolás) + dupla kattintás = default + mértékegységes kijelzés. | Nem |
| **P2** | Preset import fájlból: a mentésre épül. Termék- és verzióellenőrzés úgy, mint a „Preset Error Product/Version” a QuickTone-ban. | A mentés után nem |
| **P2** | Slotcsere (CMP↔EFX, EFX↔MOD, RVB↔DLY) a UI-ban: az alt bit ellenőrzése. | Kicsi |
| **P2** | Blokksorrend drag&drop: `0D op01`. | Közepes |
| **P2** | USB audio beállítások (`14`): rec/playback szint, routing mód, saját ábrákkal. | Kicsi-közepes |
| **P2** | Drum + Looper panel (`19`, `18`): get dekódolása, set tesztelése. | Közepes |
| **P2** | EXP/footswitch/WAH + globális beállítások (Global EQ, kimeneti mód, szintek; system set, a `13` nem támogatott). | Közepes |
| **P2** | Custom MIDI tábla megjelenítése (`0F`, read-only), utána írás. | Kicsi / közepes |
| **P3** | User IR kezelés (`09`/`10`, CRC, WAV import/export, „paste to presets”). | **Igen**, nagy |
| **P3** | NAM („BlackBox”) betöltés + NAM modellindexek. | **Igen**, nagy |
| **P3** | Boot-up kép (800×480). | **Igen**, nagy |
| **P3** | Gyári visszaállítás (összes preset). | **Igen**, kockázatos. Helyette backup + restore a saját mentéseinkből. |
| **P3** | Lokalizáció (gettext, magyar + angol). | Nem |

## 5. Webes források (kivonat)

- Termékoldal: https://www.nuxaudio.com/?p=3435. Két szoftverpárost kínál. A **GEN2** fw V3.0.0 (2026-07-10) **csak** a QuickTone 2.6.7.16-tal működik. A V2 fw V2.0.0 a QuickTone 2.6.4.14-gyel. A QuickTone-hoz nincs nyilvános changelog.
- A firmware-t a külön NUX Device Updater tölti fel DFU módban (mindkét footswitch nyomva bekapcsoláskor). A frissítés törli a user beállításokat, ezért előtte backup kell, és ez a mi P1 backup-funkciónk mellett szól. Útmutató: https://nux.cherubtechnology.com/download/Firmware/Effects/MultiEffects/MG300MKII/Guide/NUX_MG300MKII_FirmwareUpdateGuide_V2_EN.pdf
- Manual: https://nux.cherubtechnology.com/download/Manual/Effects/Multi_Effects/MG300MKII/NUX_MG300MKII_UserManual_English.pdf
  - 10 blokk, szabad sorrend, 3 Pro Scene/preset, 24 user IR (512 minta).
  - USB: NORMAL / REAMP / DRY OUT / Loopback.
  - Looper: 60 s stereo vagy 120 s mono. Drum gép, tuner, tap tempo.
  - EXP célok, footswitch módok (Preset / Scene / Scene+Preset, S3 kikapcsolható).
  - 5 kimeneti mód, Global EQ.
  - GEN2 újdonságok: 10 NAM slot (Gain/Bass/Mid/Treble/Master), 4 wah, 3 „DeepImage” amp.
- Import-folyamat (Choptones): Import gomb az eszköztáron → fájl → cél bank/slot → megerősítés. User IR: jobb klikk a cab blokkon → User → slot → Import. https://www.choptones.com/blogs/tutorials/elevate-your-sound-with-nux-devices-a-simple-guide-to-load-patches-and-irs
- Factory presetek szürkével, user presetek fehérrel; patch másolás és csere slotok között (Equipboard, Reddit-összefoglaló): https://equipboard.com/items/nux-mg-300-mkii-multi-effects-and-modeling-processor
- Audiofanzine teszt: a pluszok között „well-designed editing software”, a pedálon szerkeszteni „a real pain”. https://en.audiofanzine.com/electric-guitar-multi-effects/nux/mg-300-mk2/editorial/reviews/test-the-nux-mg-300-mkii-multi-effects-pedalboard.html
- Panaszok (főleg MG-30 / MG-300 v1): „searching for device” elakadás, driver- és verzió-egyezési gondok, nincs Linux-támogatás, Wine alatt nem csatlakozik. https://blog.nostatic.org/2021/01/nux-mg-300-guitar-processor-under-linux.html. Ezek a mi létjogosultságunkat erősítik.
- Videó vagy cikk, amely a QuickTone MG-300 MKII felületét részletesen bemutatná, nem található. A design-leírás ezért a binárisból kinyert grafikákon alapul.
