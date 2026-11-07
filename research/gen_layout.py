"""Generate layout_draft.json for the MG-300 MKII 126-byte scene.

Inputs (data only): QuickTone MG300MK2 DisplayConfig JSON, MG-300V2 model list lines,
sample scene. Output: layout_draft.json.
"""
import json, re, sys

disp_path, lists_path, scene_path, out_path = sys.argv[1:5]
disp = json.load(open(disp_path))
lines = [l.rstrip('\n') for l in open(lists_path)]
scene = open(scene_path, 'rb').read()
assert len(scene) == 126

# ---- split QuickTone MG-300V2 dropdown lists -------------------------------------------
assert lines[0] == 'NUX MG-300V2'
lists, cur = [], None
for l in lines[1:]:
    if l in ('BYPASS', 'COLLECTION'):
        if cur is not None:
            lists.append(cur)
        cur = [] if l == 'BYPASS' else None
        if l == 'COLLECTION':
            cur = []  # mic list follows
        continue
    m = re.match(r'(\d+)\. (.*)', l)
    if m and cur is not None:
        cur.append(m.group(2))
if cur:
    lists.append(cur)
names = ['CMP', 'NR', 'PL', 'EFX', 'EFX_ALT_IN_CMP', 'AMP', 'CAB', 'MIC', 'EQ', 'MOD',
         'MOD_ALT_IN_EFX', 'DLY', 'DLY_ALT_IN_RVB', 'RVB']
assert len(lists) == len(names), [len(x) for x in lists]
L = dict(zip(names, lists))

# ---- per-block fixed slot areas (decoded scene offsets) --------------------------------
# block: (chain_id, model_offset, count_offset, n_slots, cc_switch, cc_param_base)
BLK = {
    'WAH': (0, 0x00, 0x0C, 2, None, None),
    'CMP': (1, 0x01, 0x0F, 4, 1, 14),
    'EFX': (2, 0x02, 0x14, 6, 2, 18),
    'AMP': (3, 0x03, 0x1B, 8, 3, 24),
    'EQ':  (4, 0x04, 0x24, 12, 4, 32),
    'NR':  (5, 0x05, 0x31, 4, 5, 44),
    'MOD': (6, 0x06, 0x36, 6, 6, 48),
    'DLY': (7, 0x07, 0x3D, 8, 7, 54),
    'RVB': (8, 0x08, 0x46, 4, 8, 62),
    'CAB': (9, 0x09, 0x4B, 6, 9, 66),
}

KIND = {2: 'knob', 3: 'fader', 7: 'toggle01', 6: 'switch', 5: 'button', 9: 'knob', 90: 'selector', 4: None, 10: None, 97: None}


def disp_params(block, idx, name):
    key = f'{idx}. {name}'
    if key not in disp:
        return None
    out = {}
    for e in disp[key]['array']:
        cc = e['midiCC']
        if cc >= 50:          # 51..60 = block on/off indicator (50 + chain id)
            continue
        kind = KIND.get(e['elementType'], 'other')
        if kind is None:
            continue
        lk = e['languageKey']
        pname = lk.split('_', 1)[1] if lk else None
        p = out.get(cc, {})
        if pname or 'name' not in p:
            p['name'] = pname or p.get('name')
        p.setdefault('kind', kind)
        if kind in ('knob', 'fader', 'toggle01'):
            p['kind'] = kind
        p['min'], p['max'] = e['parameter1'], e['parameter2']
        if e['parameter3'] != -1:
            p['default'] = e['parameter3']
        out[cc] = p
    return out


# names for unnamed UI elements (from MG-30/MG-400 docs + element images)
SPECIAL = {
    ('EQ', '6-EQ'): ['100HZ', '220HZ', '500HZ', '1.2KHZ', '2.6KHZ', '6.4KHZ'],
    ('EQ', '10-EQ'): ['VOL', '31HZ', '62HZ', '125HZ', '250HZ', '500HZ', '1KHZ', '2KHZ', '4KHZ', '8KHZ', '16KHZ', 'GAIN'],
    ('MOD', 'HARMONY'): {0: 'KEY', 1: 'MINOR/MAJOR'},
    ('MOD', 'U-VIBE'): {3: 'CHORUS/VIBRATO'},
    ('EFX', 'TOUCH WAH'): {3: 'UP/DOWN'},
    ('MOD', 'CHORUS+'): {2: 'MODE (button)'},
}
SCALE = {('EQ', '6-EQ'): 'dB = -15 + 0.3*v (per MG-400 notes, unverified)',
         ('EQ', '10-EQ'): 'dB = -12 + 0.24*v (per MG-400 notes, unverified)'}


def build_models(block, names_list, alt=False):
    chain, moff, coff, nslots, _, _ = BLK[block]
    slots = [coff + 1 + i for i in range(nslots)]
    models = []
    for i, n in enumerate(names_list, 1):
        m = {'index': i, 'byte_value_on': i | (0x20 if alt else 0), 'name': n}
        src_block = block
        ps = disp_params(src_block, i, n)
        params = []
        if ps:
            for slot in sorted(ps):
                p = ps[slot]
                d = {'name': p.get('name'), 'slot': slot, 'offset': slots[slot]}
                d.update({k: p[k] for k in ('min', 'max', 'default') if k in p})
                d['kind'] = p['kind']
                params.append(d)
        sp = SPECIAL.get((block, n))
        if isinstance(sp, list):
            for d, nm in zip(params, sp):
                d['name'] = nm
        elif isinstance(sp, dict):
            for d in params:
                if d['slot'] in sp and not d['name']:
                    d['name'] = sp[d['slot']]
        if (block, n) in SCALE:
            m['scale'] = SCALE[(block, n)]
        m['params'] = params
        models.append(m)
    return models


cab_params = [
    {'name': 'MIC', 'slot': 0, 'offset': 0x4C, 'min': 0, 'max': 3, 'choices': L['MIC'], 'kind': 'selector'},
    {'name': 'MIC POSITION', 'slot': 1, 'offset': 0x4D, 'min': 0, 'max': 2, 'choices': ['EDGE', 'MIDDLE', 'CENTER'], 'kind': 'selector'},
    {'name': 'LEVEL', 'slot': 2, 'offset': 0x4E, 'min': 0, 'max': 100, 'default': 50, 'kind': 'knob', 'scale': 'dB = -12 + 0.24*v (MG-400 notes, unverified)'},
    {'name': 'LOW CUT', 'slot': 3, 'offset': 0x4F, 'min': 0, 'max': 100, 'default': 0, 'kind': 'knob', 'scale': 'Hz = 20*50**(v/100) (MG-400 notes, unverified)'},
    {'name': 'HIGH CUT', 'slot': 4, 'offset': 0x50, 'min': 0, 'max': 100, 'default': 100, 'kind': 'knob', 'scale': 'Hz = 5000*4**(v/100) (MG-400 notes, unverified)'},
]

CONF = {
    'WAH': ('guess', 'Model byte offset 0 and slot area 0x0C-0x0E follow MG-30 layout; firmware V3.0.0 notes add "WAH (4 models)" but QuickTone 2.6.7.16 has no MG-300V2 WAH list. Model names below are the MG-30 wah names (guess). Not part of block_order list (pedal-controlled, fixed position?).'),
    'CMP': ('verified', 'model offset verified by QuickTone file diff (forge cal_07); params: likely'),
    'EFX': ('verified', 'model offset verified by file diff (forge cal_11); params: likely (QuickTone UI config)'),
    'AMP': ('verified', 'model offset + slots 0-5 verified by file diff (forge cal_01..06, cal_22/23); per-model names from QuickTone UI config'),
    'EQ': ('likely', 'offset from MG-30/MG-400 index=CC rule; sample value 01 (6-EQ on) fits'),
    'NR': ('likely', 'offset from MG-30/MG-400 rule; sample count byte 2 = NR knob count'),
    'MOD': ('verified', 'model offset verified by file diff (forge cal_20/21); params likely'),
    'DLY': ('verified', 'model offset verified by file diff (forge cal_09); params likely'),
    'RVB': ('verified', 'model offset verified; slot 3 (0x4A) verified as HALL LEVEL by file diff (forge cal_08)'),
    'CAB': ('verified', 'model offset verified (forge selector tests); param names from MG-400 docs (likely); sample: MIC=3 SM57, POS=2, LEVEL=50, LOWCUT=0, HIGHCUT=100'),
}

blocks = []
for b in ['WAH', 'CMP', 'EFX', 'AMP', 'EQ', 'NR', 'MOD', 'DLY', 'RVB', 'CAB']:
    chain, moff, coff, nslots, ccs, ccp = BLK[b]
    ent = {
        'id': b, 'chain_id': chain, 'model_offset': moff, 'bypass_bit': 64,
        'model_mask': 0x1F if b in ('CMP', 'EFX', 'MOD', 'RVB') else 0x3F,
        'count_offset': coff,
        'param_offsets': [coff + 1 + i for i in range(nslots)],
        'cc_switch': ccs,
        'cc_params': [ccp + i for i in range(nslots)] if ccp is not None else None,
        'confidence': CONF[b][0], 'notes': CONF[b][1],
    }
    if b == 'WAH':
        ent['models'] = [{'index': i, 'name': n, 'params': []} for i, n in enumerate(['CLYDE', 'CRY BB', 'V847', 'HORSE WAH'], 1)]
    elif b == 'CAB':
        ent['models'] = [{'index': i, 'name': n} for i, n in enumerate(L['CAB'], 1)]
        ent['models_extra_guess'] = 'QuickTone also lists COLLECTION (24 IR collection slots) and USER IR; their model byte values are unknown (37+?)'
        ent['block_params'] = cab_params
    else:
        key = {'NR': 'NR'}.get(b, b)
        ent['models'] = build_models(b, L[key])
        if b == 'AMP':
            ent['models_extra_guess'] = 'Firmware V3.0.0 adds 10 NAM slots in AMP (QuickTone NAM panel: GAIN,BASS,MIDDLE,TREB,MASTER at slots 0-4, default 50). By analogy with MG-400 (32 amps, NAM = 33..42) NAM 1-10 are probably model 31..40. Unverified.'
            nam = disp_params('AMP', 0, 'NAM')
            ent['nam_params'] = [{'name': 'GAIN', 'slot': 0}, {'name': 'BASS', 'slot': 1}, {'name': 'MIDDLE', 'slot': 2}, {'name': 'TREB', 'slot': 3}, {'name': 'MASTER', 'slot': 4}]
            for p in ent['nam_params']:
                p.update({'offset': ent['param_offsets'][p['slot']], 'min': 0, 'max': 100, 'default': 50})
    alt = {'CMP': ('EFX', 'EFX_ALT_IN_CMP'), 'EFX': ('MOD', 'MOD_ALT_IN_EFX'), 'RVB': ('DLY', 'DLY_ALT_IN_RVB')}.get(b)
    if alt:
        ab, key = alt
        am = build_models(ab, L[key], alt=True)
        # alt params live in THIS block's slots
        for m in am:
            for p in m['params']:
                p['offset'] = ent['param_offsets'][p['slot']] if p['slot'] < nslots else None
            m['params'] = [p for p in m['params'] if p['offset'] is not None]
        ent['alt_type'] = {
            'hosts': ab, 'flag_bit': 32,
            'confidence': 'likely',
            'notes': 'QuickTone "Hold: %s<-->%s" swap; byte = alt-list index | 0x20 (| 0x40 if bypassed). Rule taken from MG-400 (+32 on the switch CC). Which QuickTone list belongs to which host slot is inferred (list without TOUCH WAH / RTY SPK = alt list).' % (b, ab),
            'models': am,
        }
    blocks.append(ent)

# PL / patch-volume area
blocks.append({
    'id': 'PL', 'chain_id': 10, 'model_offset': 0x0A, 'bypass_bit': 64, 'model_mask': 0x3F,
    'models': [{'index': 1, 'name': 'PATCH LEVEL'}],
    'cc_switch': 10,
    'params': [
        {'name': 'SEND (vestigial MG-30 S/R?)', 'offset': 0x53, 'cc': 72, 'min': 0, 'max': 100, 'observed': 50, 'confidence': 'guess'},
        {'name': 'RETURN (vestigial MG-30 S/R?)', 'offset': 0x54, 'cc': 73, 'min': 0, 'max': 100, 'observed': 50, 'confidence': 'guess'},
        {'name': 'PATCH MIN', 'offset': 0x56, 'cc': 74, 'min': 0, 'max': 100, 'observed': 0, 'confidence': 'likely'},
        {'name': 'PATCH MAX', 'offset': 0x57, 'cc': 75, 'min': 0, 'max': 100, 'observed': 100, 'confidence': 'likely'},
        {'name': 'PATCH LEVEL', 'offset': 0x58, 'cc': 76, 'min': 0, 'max': 100, 'observed': 50, 'default': 50, 'confidence': 'likely'},
        {'name': 'P.L POSITION', 'offset': 0x59, 'cc': 77, 'min': 0, 'max': 1, 'observed': 0x80, 'confidence': 'guess', 'notes': 'observed 0x80; maybe value 0 with flag bit 7 (MG-30 has 0x80 S/R routing byte nearby)'},
    ],
    'count_offsets': [0x52, 0x55],
    'confidence': 'likely',
    'notes': 'Switch byte 0x0A verified only as "01" in samples; chain id 10 (0x0A) appears last in block order. Area mirrors MG-30 S/R(82-85)+VOL(86-89) with one byte less.',
})

# annotate sample values
def annotate(ent):
    if 'models' not in ent:
        return
for ent in blocks:
    if 'param_offsets' in ent:
        ent['sample_scene_a'] = {'model_byte': scene[ent['model_offset']], 'count': scene[ent['count_offset']],
                                 'slots': [scene[o] for o in ent['param_offsets']]}

layout = {
    'device': 'NUX MG-300 MKII (USB name MG-300V2), firmware V3.0.0',
    'status': 'DRAFT - see layout_notes.md for evidence and confidence; confirm with hardware diff tests',
    'scene_size': 126,
    'offsets_are': 'decimal byte offsets into the decoded 126-byte scene (0C/0B payload after 8->7 bit unpacking)',
    'model_byte': {'index_bits': '0x1F (alt-capable slots) / 0x3F (others), 1-based QuickTone menu position', 'alt_type_bit': 32, 'bypass_bit': 64,
                   'confidence': 'verified (index+bypass via file diffs), alt bit likely'},
    'count_byte': {'note': 'Each block param area starts with a byte that usually equals the number of knobs of the current model (MG-30 docs). Observed mismatches (HALL shows 2, SPRING shows 3, CLASS A30 shows 6). Treat as opaque; preserve on write. Unknown whether the pedal needs it updated when the model changes.', 'confidence': 'likely'},
    'switch_slots': {'0x00': 'WAH', '0x01': 'CMP', '0x02': 'EFX', '0x03': 'AMP', '0x04': 'EQ', '0x05': 'NR', '0x06': 'MOD', '0x07': 'DLY', '0x08': 'RVB', '0x09': 'CAB/IR', '0x0A': 'PL', '0x0B': 'unknown (MG-30 VOL switch; QuickTone CC11 label MIC); observed 0x41'},
    'name': {'offset': 105, 'length': 16, 'encoding': 'ASCII, NUL padded', 'confidence': 'verified'},
    'tempo': {'offset': 92, 'hi_offset': 91, 'formula': 'bpm = (b[91] << 7) | b[92]  (MG-30 convention; b[91] observed 0 at 120 bpm)', 'confidence': 'verified for offset 92 low byte; hi byte likely'},
    'block_order': {'offset': 93, 'length': 10, 'reserved_length': 12, 'padding': [103, 104],
                    'ids': {'1': 'CMP', '2': 'EFX', '3': 'AMP', '4': 'EQ', '5': 'NR', '6': 'MOD', '7': 'DLY', '8': 'RVB', '9': 'CAB', '10': 'PL'},
                    'default': [5, 1, 2, 3, 9, 4, 6, 7, 8, 10],
                    'confidence': 'verified (matches cmd 0D); id meanings likely (= switch offset = CC#, matches MG-400 default chain GATE CMP EFX AMP IR EQ MOD DLY RVB PL)'},
    'blocks': blocks,
    'unknown_offsets': [
        {'offset': 0x0B, 'observed': scene[0x0B], 'guess': 'switch slot 11 (MG-30: VOL block switch)'},
        {'offset': 0x0C, 'observed': scene[0x0C], 'guess': 'WAH knob count'},
        {'offset': 0x0D, 'observed': scene[0x0D], 'guess': 'WAH slot 0'},
        {'offset': 0x0E, 'observed': scene[0x0E], 'guess': 'WAH slot 1'},
        {'offset': 0x52, 'observed': scene[0x52], 'guess': 'count byte for PL/(S/R) area'},
        {'offset': 0x53, 'observed': scene[0x53], 'guess': 'Send (CC72) vestigial'},
        {'offset': 0x54, 'observed': scene[0x54], 'guess': 'Return (CC73) vestigial'},
        {'offset': 0x55, 'observed': scene[0x55], 'guess': 'count byte for patch volume area'},
        {'offset': 0x59, 'observed': scene[0x59], 'guess': 'P.L Position (CC77) and/or routing flag'},
        {'offset': 0x5A, 'observed': scene[0x5A], 'guess': 'unknown (MG-30 offset 90 also 00)'},
        {'offset': 0x5B, 'observed': scene[0x5B], 'guess': 'tempo high 7 bits'},
        {'offset': 0x67, 'observed': scene[0x67], 'guess': 'block-order padding (MG-30 has 12 chain entries)'},
        {'offset': 0x68, 'observed': scene[0x68], 'guess': 'block-order padding'},
        {'offset': 0x79, 'observed': scene[0x79], 'guess': 'MG-30 has CTRL/EXP assignments after the name; maybe EXP/footswitch assignment'},
        {'offset': 0x7A, 'observed': scene[0x7A], 'guess': 'as above'},
        {'offset': 0x7B, 'observed': scene[0x7B], 'guess': 'as above'},
        {'offset': 0x7C, 'observed': scene[0x7C], 'guess': 'as above'},
        {'offset': 0x7D, 'observed': scene[0x7D], 'guess': 'as above (observed 01 in every scene seen; maybe EXP pedal target = block 1?)'},
        {'offset': 'unused slots', 'guess': 'param slots beyond the current model hold stale values from previous models (e.g. CMP slot2=69, AMP slot5=69, RVB slots2-3). Preserve them.'},
    ],
}
json.dump(layout, open(out_path, 'w'), indent=1)
print('ok', len(blocks))
for b in blocks:
    print(b['id'], len(b.get('models', [])), b.get('sample_scene_a'))
