"""Landscape panel: a landscape material put on custom meshes, set up from one place.

Layers: each paint layer with its channel; Paint (its weightmap in Texture Paint, or its colour attribute in Vertex
Paint, the brush in its channel's colour), Fill, Clear, Normalize. Features: the material's static switches (snow on a
biome, scree, biome masks from paint...), applied by Update Material. Parameters: the material's own inputs."""
import functools
import json

import bpy

CHANNEL_COLORS = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
EDITS = "mp_switch_edits"       # on the material: switches changed in the panel, waiting for Update Material

_loads = functools.lru_cache(maxsize=32)(json.loads)     # the panel reads the switches on every redraw


def _landscape(context):
    obj = context.active_object
    return obj if obj is not None and obj.type == 'MESH' and obj.get("mp_landscape") else None


def switches(mat):
    """{switch: on} of the material as built (shared between calls: read it, don't change it)."""
    return _loads(mat.get("mp_switches") or "{}") if mat is not None else {}


def features(mat):
    """[(switch, on)] as the panel shows them: built, or as edited since."""
    edits = dict(mat.get(EDITS) or {}) if mat is not None else {}
    return [(name, bool(edits.get(name, on))) for name, on in switches(mat).items()]


def _brush_color(context, paint, rgb):
    """The brush colour of a paint mode's settings (the unified colour too, whichever this Blender paints with)."""
    for settings in (getattr(paint, "unified_paint_settings", None), getattr(context.tool_settings, "unified_paint_settings", None)):
        if settings is not None and hasattr(settings, "color"):
            settings.color = rgb
    brush = getattr(paint, "brush", None)
    if brush is not None:
        try:
            brush.color = rgb
        except AttributeError:
            pass        # an asset brush not made local: the unified colour paints


def _new_layers(mat, obj):
    """Layers a rebuilt material reads that the meshes' weightmaps lack, added to them; whether there were any."""
    from ..material_porter import landscape_layers, weightmaps
    from ..material_porter.material_onto import _layers
    missing = sorted(_layers(mat) - set(landscape_layers.table(obj)))
    if not missing or not weightmaps.images(obj):
        return False
    meshes = [o for o in bpy.context.selected_objects if o.type == 'MESH' and o.get(weightmaps.SET) == obj.get(weightmaps.SET)]
    weightmaps.ensure(meshes or [obj], missing)
    return True


def _textures(mat):
    """The material's image nodes, by the input they feed on its root group (its texture parameters), else by
    label: [(name, image node)]."""
    node = _root_node(mat)
    out, seen = [], set()
    if node is not None:
        for sock in node.inputs:
            for link in sock.links:
                n = link.from_node
                while n.bl_idname == "NodeReroute" and n.inputs[0].links:
                    n = n.inputs[0].links[0].from_node
                if n.bl_idname == "NodeClosureOutput":       # a texture passed as a sampler: the image inside
                    n = next((l.from_node for i in n.inputs for l in i.links), n)
                if n.bl_idname == "ShaderNodeTexImage" and n.name not in seen:
                    seen.add(n.name)
                    out.append((sock.name, n))
    return out


def _root_node(mat):
    return next((n for n in mat.node_tree.nodes if n.type == 'GROUP'), None) if mat and mat.node_tree else None


class FPMP_OT_PaintLandscapeLayer(bpy.types.Operator):
    """Paint this layer: its weightmap (Texture Paint) or colour attribute (Vertex Paint) made active, the brush set to
    its channel (red, green or blue; painting it lowers the other two layers sharing its image, as layers blend)"""
    bl_idname = "fpmp.paint_landscape_layer"
    bl_label = "Paint Layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return _landscape(context) is not None

    def execute(self, context):
        from ..material_porter import landscape_layers, weightmaps
        obj = context.active_object
        where = landscape_layers.table(obj).get(self.layer)
        if where is None:
            self.report({'WARNING'}, "%s isn't a layer of %s" % (self.layer, obj.name))
            return {'CANCELLED'}
        name, channel = where
        mesh = obj.data
        if (image := weightmaps.images(obj).get(name)) is not None:
            if weightmaps.UV in mesh.uv_layers:
                mesh.uv_layers.active = mesh.uv_layers[weightmaps.UV]
            if obj.mode != 'TEXTURE_PAINT':
                bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
            paint = context.tool_settings.image_paint
            paint.mode, paint.canvas = 'IMAGE', image
        else:
            mesh.color_attributes.active_color = mesh.color_attributes[name]
            if obj.mode != 'VERTEX_PAINT':
                bpy.ops.object.mode_set(mode='VERTEX_PAINT')
            paint = context.tool_settings.vertex_paint
        _brush_color(context, paint, CHANNEL_COLORS[channel])
        self.report({'INFO'}, "Painting %s (%s %s)" % (self.layer, name, landscape_layers.CHANNELS[channel]))
        return {'FINISHED'}


class FPMP_OT_FillLandscapeLayer(bpy.types.Operator):
    """Fill: this layer everywhere and the others cleared (Clear: this layer removed everywhere)"""
    bl_idname = "fpmp.fill_landscape_layer"
    bl_label = "Fill Layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer: bpy.props.StringProperty()
    value: bpy.props.FloatProperty(default=1.0)

    @classmethod
    def poll(cls, context):
        from ..material_porter import weightmaps
        return (obj := _landscape(context)) is not None and bool(weightmaps.images(obj))

    def execute(self, context):
        from ..material_porter import weightmaps
        weightmaps.fill(context.active_object, self.layer, self.value)
        return {'FINISHED'}


class FPMP_OT_NormalizeLandscape(bpy.types.Operator):
    """Scale the layers so they add up to 1 everywhere something is painted, as UE's landscape painting keeps them"""
    bl_idname = "fpmp.normalize_landscape"
    bl_label = "Normalize Layers"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        from ..material_porter import weightmaps
        return (obj := _landscape(context)) is not None and bool(weightmaps.images(obj))

    def execute(self, context):
        from ..material_porter import weightmaps
        weightmaps.normalize(context.active_object)
        return {'FINISHED'}


def _set_meshes(obj):
    """The meshes sharing `obj`'s weightmaps (in the scene)."""
    from ..material_porter import weightmaps
    group = obj.get(weightmaps.SET)
    return [o for o in bpy.context.scene.objects if o.type == 'MESH' and group and o.get(weightmaps.SET) == group] or [obj]


class FPMP_OT_MakeBiomeMap(bpy.types.Operator):
    """Paint this map-wide texture yourself: an image in its place, laid out like the game's (each rectangle the
    material reads, one biome per channel), across the meshes sharing these weightmaps; the material is rebuilt to read it"""
    bl_idname = "fpmp.make_biome_map"
    bl_label = "Make Paintable"
    bl_options = {'REGISTER', 'UNDO'}

    param: bpy.props.StringProperty()

    def execute(self, context):
        from ..material_porter import biome_maps
        obj = context.active_object
        info = biome_maps.maps(obj.active_material).get(self.param)
        if info is None:
            return {'CANCELLED'}
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        meshes = _set_meshes(obj)
        from ..material_porter import biome_colours
        game = next((n.image for name, n in _textures(obj.active_material) if name == self.param), None)             or biome_colours.game_image(info)
        if biome_maps.create(meshes, self.param, info, game) is None:
            self.report({'WARNING'}, "No weightmaps on %s: put the landscape material on it again" % obj.name)
            return {'CANCELLED'}
        for o in context.selected_objects:
            o.select_set(False)
        for o in meshes:
            o.select_set(True)
        context.view_layer.objects.active = obj
        return bpy.ops.fpmp.update_landscape_material()


class FPMP_OT_RemoveBiomeMap(bpy.types.Operator):
    """Remove this painted map: the material reads the game's again (Make Paintable starts a new one)"""
    bl_idname = "fpmp.remove_biome_map"
    bl_label = "Remove Painted Map"
    bl_options = {'REGISTER', 'UNDO'}

    param: bpy.props.StringProperty()

    def execute(self, context):
        from ..material_porter import biome_maps
        obj = context.active_object
        if obj.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        meshes = _set_meshes(obj)
        name = dict(obj.get(biome_maps.KEY) or {}).get(self.param)
        for o in meshes:
            found = dict(o.get(biome_maps.KEY) or {})
            found.pop(self.param, None)
            o[biome_maps.KEY] = found
            for layer in [l for l in o.data.uv_layers if l.name.startswith(biome_maps.uv_name(self.param, 0)[:-1])]:
                o.data.uv_layers.remove(layer)
        if name and bpy.data.images.get(name) is not None:
            bpy.data.images.remove(bpy.data.images[name])
        for o in context.selected_objects:
            o.select_set(False)
        for o in meshes:
            o.select_set(True)
        context.view_layer.objects.active = obj
        return bpy.ops.fpmp.update_landscape_material()


class FPMP_OT_PaintBiome(bpy.types.Operator):
    """Paint where this biome is (Texture Paint; Ctrl to erase). Biomes painted on top of each other replace each
    other"""
    bl_idname = "fpmp.paint_biome"
    bl_label = "Paint Biome"
    bl_options = {'REGISTER', 'UNDO'}

    param: bpy.props.StringProperty()
    index: bpy.props.IntProperty()
    channel: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        from ..material_porter import biome_maps
        obj = context.active_object
        image = bpy.data.images.get(dict(obj.get(biome_maps.KEY) or {}).get(self.param, ""))
        layer = obj.data.uv_layers.get(biome_maps.uv_name(self.param, self.index))
        if image is None or layer is None:
            return {'CANCELLED'}
        obj.data.uv_layers.active = layer
        if self.channel < 0:
            from ..material_porter import biome_colours
            which = biome_colours.pair(obj)
            if which and which[1] == self.param and obj.get(biome_colours.FOLLOW, True):
                bpy.ops.fpmp.follow_biomes()        # painted by hand from now on, from the colours it showed
                obj = context.active_object
                layer = obj.data.uv_layers.get(biome_maps.uv_name(self.param, self.index))
                obj.data.uv_layers.active = layer
        if obj.mode != 'TEXTURE_PAINT':
            bpy.ops.object.mode_set(mode='TEXTURE_PAINT')
        paint = context.tool_settings.image_paint
        paint.mode, paint.canvas = 'IMAGE', image
        brush = getattr(paint, "brush", None)
        if 0 <= self.channel < 3:
            _brush_color(context, paint, CHANNEL_COLORS[self.channel])
            for settings in (getattr(paint, "unified_paint_settings", None), brush):
                if settings is not None and hasattr(settings, "secondary_color"):
                    settings.secondary_color = (0.0, 0.0, 0.0)     # Ctrl erases
        if brush is not None:
            # a red, green or blue mask leaves alpha (another biome) alone; alpha's is painted on its own
            for attr, value in (("blend", 'ADD_ALPHA' if self.channel == 3 else 'MIX'),
                                ("use_alpha", self.channel < 0 or self.channel == 3)):
                try:
                    setattr(brush, attr, value)
                except (AttributeError, TypeError):
                    pass
        return {'FINISHED'}


class FPMP_OT_FillBiome(bpy.types.Operator):
    """This biome everywhere, every other cleared (Clear All: no biome anywhere, the material's default ground)"""
    bl_idname = "fpmp.fill_biome"
    bl_label = "Fill Biome"
    bl_options = {'REGISTER', 'UNDO'}

    param: bpy.props.StringProperty()
    index: bpy.props.IntProperty()
    channel: bpy.props.IntProperty()

    def execute(self, context):
        from ..material_porter import biome_maps
        obj = context.active_object
        info = biome_maps.maps(obj.active_material).get(self.param)
        if info is not None:
            biome_maps.fill(obj, self.param, self.index, self.channel, info)
        return {'FINISHED'}


class FPMP_OT_FollowBiomes(bpy.types.Operator):
    """Grass colours follow the biomes you paint: each biome's colour taken from the game's map, plain grassland
    where none is painted. Off: paint the colours yourself"""
    bl_idname = "fpmp.follow_biomes"
    bl_label = "Grass Follows Biomes"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        return _set_follow(obj, not _follows(obj))


def _follows(obj):
    from ..material_porter import biome_colours
    return bool(obj.get(biome_colours.FOLLOW, True))


def _set_follow(obj, on):
    from ..material_porter import biome_colours
    if not on:
        biome_colours.bake(obj)         # the colours it showed, to paint over
    for o in _set_meshes(obj):
        o[biome_colours.FOLLOW] = on
    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    return bpy.ops.fpmp.update_landscape_material()


def _follow_set(obj, on):
    if on != _follows(obj):
        _set_follow(obj, on)


class FPMP_OT_LandscapeSwitch(bpy.types.Operator):
    """Turn this feature of the material on or off (applied by Update Material)"""
    bl_idname = "fpmp.landscape_switch"
    bl_label = "Material Feature"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    name: bpy.props.StringProperty()
    on: bpy.props.BoolProperty()

    def execute(self, context):
        mat = context.active_object.active_material
        edits = dict(mat.get(EDITS) or {})
        if switches(mat).get(self.name) == self.on:
            edits.pop(self.name, None)
        else:
            edits[self.name] = self.on
        mat[EDITS] = edits
        return {'FINISHED'}


class FPMP_OT_UpdateLandscapeMaterial(bpy.types.Operator):
    """Rebuild the selected landscapes' exact materials: with the features changed in the panel, for the layers
    painted now (the FP app must be running)"""
    bl_idname = "fpmp.update_landscape_material"
    bl_label = "Update Material"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' and o.get("mp_landscape") for o in context.selected_objects)

    def execute(self, context):
        import types
        from ..material_porter import build, hook
        if context.active_object is not None and context.active_object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        rebuilt, skipped = {}, []
        for o in [o for o in context.selected_objects if o.type == 'MESH' and o.get("mp_landscape")]:
            for slot in o.material_slots:
                mat = slot.material
                path = mat.get(build.KEY_PATH) if mat is not None else None
                if not path:
                    continue
                new = rebuilt.get(mat.name)
                if new is None:
                    job = types.SimpleNamespace(options={"RimLight": bool(mat.get("MPRimLight"))},
                                                type=types.SimpleNamespace(name="MESH"))
                    overlay = json.loads(mat.get(hook.KEY_OVERLAY) or "{}")
                    if edits := dict(mat.get(EDITS) or {}):
                        overlay["switches"] = dict(overlay.get("switches") or {}, **{k: bool(v) for k, v in edits.items()})
                    data = {"Path": path, "Name": mat.get("OriginalName") or mat.name, "MPOverlay": overlay}
                    new = hook.build_exact(job, data, obj=o)
                    if new is not None and _new_layers(new, o):
                        # a feature brought in layers (snow on BiomeC): in the weightmaps, then built for them
                        new = hook.build_exact(job, data, obj=o)
                    if new is None:
                        skipped.append(mat.name)
                        continue
                    for key in ("Hash", "OriginalName", "MPRimLight", "MPSubsurface"):
                        if key in mat and key not in new:
                            new[key] = mat[key]
                    if EDITS in new:
                        del new[EDITS]
                    rebuilt[mat.name] = new
                slot.material = new
        if skipped:
            self.report({'WARNING'}, "Not rebuilt (is the FP app running?): %s" % ", ".join(sorted(set(skipped))))
        else:
            self.report({'INFO'}, ", ".join(sorted(m.name for m in rebuilt.values())) or "Nothing to rebuild")
        return {'FINISHED'}


class _LandscapePanel:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Fortnite Porting"

    @classmethod
    def poll(cls, context):
        return _landscape(context) is not None


class FPMP_PT_LandscapeLayers(_LandscapePanel, bpy.types.Panel):
    bl_label = "Landscape"
    bl_idname = "FPMP_PT_landscape_layers"
    bl_order = 1

    def draw(self, context):
        pass        # all in its sub-panels (Blender requires a draw)


class FPMP_PT_LandscapePaint(_LandscapePanel, bpy.types.Panel):
    bl_label = "Layers"
    bl_idname = "FPMP_PT_landscape_paint"
    bl_parent_id = FPMP_PT_LandscapeLayers.bl_idname
    bl_order = 0

    @classmethod
    def poll(cls, context):
        from ..material_porter import landscape_layers
        return (obj := _landscape(context)) is not None and bool(obj.get(landscape_layers.KEY))

    def draw(self, context):
        from ..material_porter import landscape_layers, weightmaps
        obj = context.active_object
        packed, maps = landscape_layers.table(obj), weightmaps.images(obj)
        canvas = context.tool_settings.image_paint.canvas
        active = obj.data.color_attributes.active_color
        col = self.layout.column(align=True)
        for layer in sorted(packed, key=lambda l: packed[l]):
            name, channel = packed[layer]
            row = col.row(align=True)
            row.label(text=layer, translate=False)
            sub = row.row(align=True)
            sub.active = False
            sub.label(text="%s %s" % (name.replace(weightmaps.PREFIX, "Map "), landscape_layers.CHANNELS[channel]), translate=False)
            painting = (obj.mode == 'TEXTURE_PAINT' and name in maps and canvas == maps[name]) or \
                       (obj.mode == 'VERTEX_PAINT' and active is not None and active.name == name)
            row.operator(FPMP_OT_PaintLandscapeLayer.bl_idname, text="", icon='BRUSH_DATA', depress=painting).layer = layer
            if maps:
                op = row.operator(FPMP_OT_FillLandscapeLayer.bl_idname, text="", icon='SNAP_FACE')
                op.layer, op.value = layer, 1.0
                op = row.operator(FPMP_OT_FillLandscapeLayer.bl_idname, text="", icon='X')
                op.layer, op.value = layer, 0.0
        if maps:
            self.layout.operator(FPMP_OT_NormalizeLandscape.bl_idname, icon='NORMALIZE_FCURVES')


class FPMP_PT_LandscapeBiomes(_LandscapePanel, bpy.types.Panel):
    """The map-wide textures the material reads (biome masks, colour maps): paintable, a biome per channel"""
    bl_label = "Biomes"
    bl_idname = "FPMP_PT_landscape_biomes"
    bl_parent_id = FPMP_PT_LandscapeLayers.bl_idname
    bl_order = 1

    @classmethod
    def poll(cls, context):
        from ..material_porter import biome_maps
        return (obj := _landscape(context)) is not None and bool(biome_maps.maps(obj.active_material))

    def draw(self, context):
        from ..material_porter import biome_colours, biome_maps
        layout = self.layout
        obj = context.active_object
        found = biome_maps.maps(obj.active_material)
        painted = dict(obj.get(biome_maps.KEY) or {})
        which = biome_colours.pair(obj, found)
        for param, info in found.items():
            colour = biome_maps.is_colour(param, info)
            row = layout.row(align=True)
            row.label(text=("Grass Colours" if "grass" in param.lower() else "Colours") if colour else "Biomes",
                      icon='COLOR' if colour else 'WORLD')
            if param not in painted:
                row.operator(FPMP_OT_MakeBiomeMap.bl_idname).param = param
                continue
            if colour:
                for i in range(len(info["rects"])):
                    op = row.operator(FPMP_OT_PaintBiome.bl_idname, text="", icon='BRUSH_DATA')
                    op.param, op.index, op.channel = param, i, -1
                if which and which[1] == param:
                    row.prop(obj, "fpmp_follow_biomes", text="", icon='LINKED', toggle=True)
            row.operator(FPMP_OT_RemoveBiomeMap.bl_idname, text="", icon='X').param = param
            if not colour:
                self._masks(layout, param, info)

    def _masks(self, layout, param, info):
        header, body = layout.panel("fpmp_biome_masks_" + param, default_closed=False)
        header.label(text="Masks")
        if body is None:
            return
        labels = info.get("labels") or {}
        rows = [(i, c, labels.get("%d,%d" % (i, c))) for i in range(len(info["rects"])) for c in range(4)]
        col = body.column(align=True)
        for i, c, name in rows:
            if name is not None or not labels:
                self._mask_row(col, param, i, c, name or "Mask %d" % (i * 4 + c + 1))
        unnamed = [(i, c) for i, c, name in rows if name is None]
        if labels and unnamed:
            header, more = body.panel("fpmp_biome_unnamed_" + param, default_closed=True)
            header.label(text="Unnamed")
            if more is not None:
                col = more.column(align=True)
                for i, c in unnamed:
                    self._mask_row(col, param, i, c, "Mask %d" % (i * 4 + c + 1))
        op = body.operator(FPMP_OT_FillBiome.bl_idname, text="Clear All")
        op.param, op.index, op.channel = param, 0, -1

    @staticmethod
    def _mask_row(col, param, index, channel, name):
        row = col.row(align=True)
        row.label(text=name, translate=False)
        op = row.operator(FPMP_OT_PaintBiome.bl_idname, text="", icon='BRUSH_DATA')
        op.param, op.index, op.channel = param, index, channel
        op = row.operator(FPMP_OT_FillBiome.bl_idname, text="", icon='SNAP_FACE')
        op.param, op.index, op.channel = param, index, channel


class FPMP_PT_LandscapeFeatures(_LandscapePanel, bpy.types.Panel):
    """The material's static switches: changed here, applied by Update Material"""
    bl_label = "Features"
    bl_idname = "FPMP_PT_landscape_features"
    bl_parent_id = FPMP_PT_LandscapeLayers.bl_idname
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 2

    def draw(self, context):
        col = self.layout.column(align=True)
        for name, on in features(context.active_object.active_material):
            row = col.row()
            row.alignment = 'LEFT'      # an operator centres its text: drawn as a checkbox, it sits left
            op = row.operator(FPMP_OT_LandscapeSwitch.bl_idname, text=name, translate=False, emboss=False,
                              icon='CHECKBOX_HLT' if on else 'CHECKBOX_DEHLT')
            op.name, op.on = name, not on
        waiting = len(context.active_object.active_material.get(EDITS) or {})
        self.layout.operator(FPMP_OT_UpdateLandscapeMaterial.bl_idname, icon='FILE_REFRESH',
                             text="Update Material (%d changed)" % waiting if waiting else "Update Material")


class FPMP_PT_LandscapeMaterial(_LandscapePanel, bpy.types.Panel):
    bl_label = "Material"
    bl_idname = "FPMP_PT_landscape_material"
    bl_parent_id = FPMP_PT_LandscapeLayers.bl_idname
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return (obj := _landscape(context)) is not None and obj.active_material is not None and obj.active_material.node_tree is not None

    def draw(self, context):
        mat = context.active_object.active_material
        node = _root_node(mat)
        if node is None:
            return
        header, body = self.layout.panel("fpmp_landscape_textures", default_closed=True)
        header.label(text="Textures")
        if body is not None:
            for name, n in _textures(mat):
                body.label(text=name, translate=False)
                body.template_ID(n, "image", new="image.new", open="image.open")
        header, body = self.layout.panel("fpmp_landscape_parameters", default_closed=True)
        header.label(text="Parameters")
        if body is not None:
            body.template_node_inputs(node)


def _pack_weightmaps(*_):
    """Weightmaps and biome maps painted since the last save go into the .blend with it (they live only there)."""
    names = set()
    for o in bpy.data.objects:
        for key in ("mp_weightmaps", "mp_biome_maps"):
            names |= set(dict(o.get(key) or {}).values())
    for name in names:
        image = bpy.data.images.get(name)
        if image is not None and image.is_dirty:
            image.pack()


classes = (FPMP_OT_PaintLandscapeLayer, FPMP_OT_FillLandscapeLayer, FPMP_OT_NormalizeLandscape, FPMP_OT_LandscapeSwitch,
           FPMP_OT_MakeBiomeMap, FPMP_OT_RemoveBiomeMap, FPMP_OT_PaintBiome, FPMP_OT_FillBiome, FPMP_OT_FollowBiomes, FPMP_OT_UpdateLandscapeMaterial,
           FPMP_PT_LandscapeLayers, FPMP_PT_LandscapePaint, FPMP_PT_LandscapeBiomes, FPMP_PT_LandscapeFeatures,
           FPMP_PT_LandscapeMaterial)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.fpmp_follow_biomes = bpy.props.BoolProperty(
        name="Grass Follows Biomes", description=" ".join(FPMP_OT_FollowBiomes.__doc__.split()),
        get=_follows, set=_follow_set, options=set())
    if _pack_weightmaps not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_pack_weightmaps)


def unregister():
    if _pack_weightmaps in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_pack_weightmaps)
    del bpy.types.Object.fpmp_follow_biomes
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
