"""Island texture recovery: FP materials from a UEFN island that came in without their textures.

FP exports only texture parameters, so UEFN island materials that hard-code textures in the parent
UMaterial, or are plain colours, arrive empty. The FP app (owner builds only) finds the source
material of each one and reports its instance parameters, its master's cooked ReferencedTextures
and parameter defaults (fork-island-* routes, IslandMaterials.cs). Textures come over the bridge's
texture route. This module picks the material, wires the textures and sets the flat colours.
(From the FP Material Fixer extension's island.py; the app's routes replace its fpisland tool.)
"""
import os
import re
from collections import defaultdict

import bpy

from . import fixer

ISLAND_FRAME = 'Island Textures'
# Helper textures referenced by Fortnite's master materials; never wire them as surface maps.
HELPER_STEMS = {'t_crack_normal', 't_crack_fx_mask', 't_blindsnormalnoise_n', 't_curie_noisepack', 't_cloud2dnoise',
                't_gaussiannoise_01_m', 't_electricnoise', 't_athena_snow_01_mobile_m'}
DIFFUSE_KEYS = ['Color', 'Base color', 'BaseColor', 'MainColor', 'DiffuseColor', 'TextureColor', 'Param']
GUID_RX = re.compile(r'[\\/]([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})[\\/]', re.I)
# Names per find request (they go in the URL).
CHUNK = 40


def _strip(name):
    return re.sub(r'(_LOD\d+)?(\.\d{3})?$', '', name)


def islands_in_file():
    """UEFN plugin GUIDs this blend already pulled textures from (FP exports to Assets/<guid>/...)."""
    return sorted({m.group(1).lower() for img in bpy.data.images if (m := GUID_RX.search(img.filepath or ''))})


def norm(p):
    """As IslandMaterials.Norm: "/53577e3c/X/Y.Y" or a file key -> "53577e3c/x/y"."""
    p = p.split('.')[0].lstrip('/').lower()
    p = re.sub(r'^fortnitegame/plugins/(gamefeatures/)?([^/]+)/content/', r'\2/', p)
    return re.sub(r'^fortnitegame/content/', 'game/', p)


# ---------------------------------------------------------------- step 1: targets
def build_targets(materials):
    """FPv4 materials whose Diffuse is still empty, with every mesh+slot using them (object slots too)."""
    targets = []
    for m in materials:
        b = fixer.base_node(m)
        if b is None or b.inputs['Diffuse'].is_linked:
            continue
        targets.append({'mat': m.name, 'orig': m.get('OriginalName', m.name)})
    names = {t['mat'] for t in targets}
    hints = defaultdict(set)
    for me in bpy.data.meshes:
        for i, mat in enumerate(me.materials):
            if mat and mat.name in names:
                hints[mat.name].add((_strip(me.name), i))
    for o in bpy.data.objects:
        if o.type == 'MESH':
            for i, s in enumerate(o.material_slots):
                if s.material and s.material.name in names:
                    hints[s.material.name].add((_strip(o.data.name), i))
    for t in targets:
        t['meshes'] = [{'mesh': a, 'slot': b} for a, b in sorted(hints[t['mat']])][:12]
    return targets


# ---------------------------------------------------------------- step 2: find the source materials in the app
def resolve(app, targets):
    """Generator yielding (done, total, label); returns one record per target
    (status: ok, ambiguous with 'alts', not found, or error).

    Candidates rank by islands this file has textures from, other islands, then game assets.
    Same-named candidates are told apart by the meshes' slots, then by leftover FP parameter values."""
    here = islands_in_file()
    lookup = {t['mat']: re.sub(r'^MID_(.+)_\d+$', r'\1', t['orig']) for t in targets}   # FP: MID_<parent>_0
    names = sorted(set(lookup.values()) | {m['mesh'] for t in targets for m in t['meshes']})
    found = {}
    for k in range(0, len(names), CHUNK):
        yield k, len(names) + len(targets), 'finding assets'
        found.update(app.get('fork-island-find', ','.join(names[k:k + CHUNK])))

    def rank(key):
        n = norm(key)
        return 0 if any(n.startswith(g) for g in here) else 1 if GUID_RX.search('/' + key + '/') else 2

    slots = {}
    failed = []

    def mesh_slots(mesh):
        if mesh not in slots:
            keys = found.get(mesh) or []
            try:
                slots[mesh] = app.get('fork-island-mesh', keys[0]) if keys else []
            except Exception as e:
                # If the app closed every mesh fails; only the first failure is logged.
                if not failed:
                    print("[FNPORTING] [Material Porter] island: %s's slots not read from the app (%s)" % (mesh, e))
                failed.append(mesh)
                slots[mesh] = []
        return slots[mesh]

    records = []
    for i, t in enumerate(targets):
        yield len(names) + i, len(names) + len(targets), t['mat']
        rec = {'mat': t['mat'], 'orig': t['orig']}
        records.append(rec)
        cands = sorted(found.get(lookup[t['mat']]) or [], key=rank)
        chosen = cands[0] if len(cands) == 1 else None
        if chosen is None and len(cands) > 1:
            for m in t['meshes']:
                mats = mesh_slots(m['mesh'])
                hit = next((c for c in cands if m['slot'] < len(mats) and norm(c) == mats[m['slot']]), None) \
                    or next((c for c in cands if norm(c) in mats), None)
                if hit:
                    chosen = hit
                    break
            rec['candidates'] = cands
        try:
            if chosen is None:
                rec['status'] = 'not found' if not cands else 'ambiguous'
                if 1 < len(cands) <= 40:
                    rec['alts'] = [app.get('fork-island-material', c) for c in cands]
                continue
            rec.update(app.get('fork-island-material', chosen))
            rec['status'] = 'ok'
        except Exception as e:
            rec['status'] = 'error: %s' % e
    return records


# ---------------------------------------------------------------- parameters
def record_params(rec):
    p = dict(rec.get('cooked', {}))
    p.update(rec.get('scalars', {}))     # instance overrides cooked defaults
    p.update(rec.get('vectors', {}))
    return p


def _close(a, b):
    if isinstance(a, list) and isinstance(b, list):
        return all(abs(x - y) < 2e-3 for x, y in zip(a, b))
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) < 2e-3
    return False


def _pick_by_fingerprint(rec, mat):
    """Same-named materials: compare the params FP left as Value/RGB nodes with each candidate."""
    alts = rec.get('alts') or []
    if not alts or not mat or not mat.node_tree:
        return None
    seen = {}
    for n in mat.node_tree.nodes:
        if n.type == 'RGB':
            seen[n.label or n.name] = list(n.outputs[0].default_value)
        elif n.type == 'VALUE':
            seen[n.label or n.name] = n.outputs[0].default_value
    if not seen:
        return None
    scored = []
    for a in alts:
        cv = dict(a.get('scalars', {}))
        cv.update(a.get('vectors', {}))
        score = sum(1 for k, v in seen.items() if k in cv and _close(v, cv[k]))
        miss = sum(1 for k in seen if k not in cv)
        scored.append((score, -miss, a))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    if scored[0][0] > 0 and (len(scored) == 1 or scored[0][:2] > scored[1][:2]):
        return scored[0][2]
    return None


def resolved_records(records):
    """Records with status ok, plus ambiguous ones that a parameter fingerprint settles."""
    out = []
    for r in records:
        if r.get('status') == 'ok':
            out.append(r)
        elif r.get('status') == 'ambiguous':
            pick = _pick_by_fingerprint(r, bpy.data.materials.get(r['mat']))
            if pick:
                merged = dict(pick)
                merged.update({'mat': r['mat'], 'orig': r['orig'], 'status': 'ok', 'picked_by': 'fingerprint'})
                out.append(merged)
    return out


# ---------------------------------------------------------------- step 3: textures
def _is_water(rec):
    return 'water' in (rec['mat'] + ' ' + ' '.join(rec.get('chain', []))).lower()


def _use_texture(rec, t):
    stem = os.path.splitext(os.path.basename(t['path']))[0].lower()
    if stem in HELPER_STEMS or stem in fixer.IMG_BLACKLIST or 'noise' in stem:
        return False
    if t['param'] != '(referenced)' or rec.get('island', True):
        return True
    # Fortnite-native parent with instance params: its referenced textures are shader helpers
    return not any(tt['param'] != '(referenced)' for tt in rec['textures'])


def apply_textures(app, records, problems):
    added, touched = 0, []
    for r in records:
        mat = bpy.data.materials.get(r['mat'])
        base = fixer.base_node(mat)
        if base is None or r['mat'] == 'DefaultMaterial' or _is_water(r):
            continue   # water masters are procedural (caustics, flow maps): nothing maps to FPv4 inputs
        nt = mat.node_tree
        touched.append(mat)
        have = {os.path.normcase(bpy.path.abspath(n.image.filepath)) for n in nt.nodes if n.type == 'TEX_IMAGE' and n.image}
        frame = next((n for n in nt.nodes if n.type == 'FRAME' and n.label == ISLAND_FRAME), None)
        seen, row = set(), 0
        for t in r.get('textures', []):
            if t['path'] in seen or not _use_texture(r, t):
                continue
            seen.add(t['path'])
            try:
                tex = app.texture(t['path'])
            except Exception as e:
                problems.append('%s: %s not exported (%s)' % (r['mat'], t['path'], e))
                continue
            f = tex.get('File')
            if not f or not os.path.exists(f) or os.path.normcase(f) in have:
                continue
            fresh = not any(os.path.normcase(bpy.path.abspath(i.filepath)) == os.path.normcase(f) for i in bpy.data.images)
            img = bpy.data.images.load(f, check_existing=True)
            if t['compression'] == 'TC_Normalmap':
                img.colorspace_settings.name = 'Non-Color'
            elif fresh:
                img.colorspace_settings.name = 'sRGB' if tex.get('Srgb') and not tex.get('Hdr') else 'Non-Color'
            if frame is None:
                frame = nt.nodes.new('NodeFrame')
                frame.label = ISLAND_FRAME
                frame.location = (base.location.x - 1100, base.location.y)
            n = nt.nodes.new('ShaderNodeTexImage')
            n.image = img
            n.label = t['param'] if t['param'] != '(referenced)' else ('Normal' if t['compression'] == 'TC_Normalmap' else '')
            n.parent = frame
            n.location = (base.location.x - 1100, base.location.y - 280 * row)
            row += 1
            added += 1
    run = fixer.fix_materials(touched, dry=False)
    return added, run


# ---------------------------------------------------------------- step 4: colours
def _is_glass(name, rec):
    return any(k in (name + ' ' + ' '.join(rec.get('chain', []))).lower() for k in ('glass', 'translucent'))


def apply_colors(records):
    log = []
    for r in records:
        name = r['mat']
        mat = bpy.data.materials.get(name)
        base = fixer.base_node(mat)
        if base is None or base.node_tree.name != 'FPv4 Base Material':
            continue
        if _is_water(r) or name == 'DefaultMaterial':
            continue   # water masters are procedural; flat colours would only hurt
        p = record_params(r)
        nt = mat.node_tree
        diff = base.inputs['Diffuse']
        glass = _is_glass(name, r)

        if not diff.is_linked:
            col = next((p[k] for k in DIFFUSE_KEYS if isinstance(p.get(k), list) and max(p[k][:3]) <= 1.001), None)
            if col is not None:
                diff.default_value = (col[0], col[1], col[2], 1.0)
                log.append(f"{name}: Diffuse = {[round(c, 3) for c in col[:3]]}")
            elif name.lower() == 'black':
                diff.default_value = (0.01, 0.01, 0.01, 1.0)
                log.append(f"{name}: Diffuse = black (by name)")

        spec = base.inputs['SpecularMasks']
        rough = p.get('Roughness') if isinstance(p.get('Roughness'), float) else None
        metal = None if glass else next((p[k] for k in ('Metallic', 'Metalic') if isinstance(p.get(k), float)), None)
        if (rough is not None or metal is not None) and not spec.is_linked and not base.inputs['SwizzleRoughnessToGreen'].is_linked:
            base.inputs['SwizzleRoughnessToGreen'].default_value = 0
            v = list(spec.default_value)
            if metal is not None: v[1] = max(0.0, min(1.0, metal))
            if rough is not None: v[2] = max(0.0, min(1.0, rough))
            spec.default_value = v
            log.append(f"{name}: roughness={rough} metallic={metal}")

        has_glass_node = any(n.type == 'GROUP' and n.node_tree and n.node_tree.name == 'FPv4 Glass' for n in nt.nodes)
        if glass and not base.inputs['Alpha'].is_linked and not has_glass_node:
            op = next((p[k] for k in ('Opacity Override', 'Opacity') if isinstance(p.get(k), float) and p[k] < 1.0), 0.15)
            base.inputs['Alpha'].default_value = op
            log.append(f"{name}: glass opacity {op}")
        if 'invisible' in name.lower():
            base.inputs['Alpha'].default_value = 0.0
            log.append(f"{name}: invisible in game -> Alpha 0")

        if not base.inputs['Emission'].is_linked:
            if isinstance(p.get('Light Color'), list):
                c = p['Light Color']
                base.inputs['Emission'].default_value = (c[0], c[1], c[2], 1)
                base.inputs['Emission Strength'].default_value = float(p.get('Intensity', 1.0))
                log.append(f"{name}: Emission = Light Color x Intensity")
            elif 'emissive' in name.lower() and isinstance(p.get('Param'), list):
                c = p['Param']
                base.inputs['Emission'].default_value = (c[0], c[1], c[2], 1)
                log.append(f"{name}: Emission = Param")

        # instance tints over a texture
        if diff.is_linked and diff.links[0].from_node.type == 'TEX_IMAGE' and r.get('vectors'):
            tex = diff.links[0].from_node
            v = r['vectors']
            tint = v.get('MainColor')
            over = v.get('OverlayColor') if isinstance(p.get('Overlay'), float) and p['Overlay'] > 0 else None
            if (tint and max(tint[:3]) < 0.999) or over:
                mix = nt.nodes.new('ShaderNodeMix'); mix.data_type = 'RGBA'
                mix.blend_type = 'MULTIPLY' if tint else 'OVERLAY'
                mix.inputs['Factor'].default_value = 1.0 if tint else float(p['Overlay'])
                mix.inputs['B'].default_value = tuple((tint or over)[:3]) + (1.0,)
                mix.label = 'UE MainColor tint' if tint else 'UE Overlay'
                mix.location = (base.location.x - 220, base.location.y + 150)
                nt.links.new(tex.outputs['Color'], mix.inputs['A'])
                nt.links.new(mix.outputs['Result'], diff)
                log.append(f"{name}: {mix.label}")
    return log
