"""Material function calls as node groups."""

from .core import (
    Attrs,
    FunctionTree,
    TexRef,
    Val,
    _Lazy,
    _Loop,
    _SpecialInputs,
    as_attrs,
    section_name,
    socket_name,
)


class FunctionsMixin:
    _scope_id = [0]

    def function_graph(self, x):
        """(name, Graph) of the function a MaterialFunctionCall calls, or None."""
        mf = x["Properties"].get("MaterialFunction") or {}
        on = mf.get("ObjectName", "")
        fname = on.split("'")[1] if "'" in on else on
        try:
            path = self.env.function(fname, mf.get("ObjectPath", ""))
        except TypeError:
            # an env that only takes the name (names can repeat across folders)
            path = self.env.function(fname)
        return (fname, self.graph(path)) if path else None

    @staticmethod
    def function_output(fg, outs, out):
        """The FunctionOutput a call's output index reads."""
        out_id = outs[out]["ExpressionOutputId"] if out < len(outs) else None
        fo = fg.outputs.get(out_id)
        if fo is None:
            fo = list(fg.outputs.values())[out] if out < len(fg.outputs) else None
        return fo

    def call(self, g, x, out, scope):
        p = x["Properties"]
        fname = (p.get("MaterialFunction") or {}).get("ObjectName", "").split("'")[1]
        outs = p.get("FunctionOutputs", [])
        # Inputs bind lazily: UE compiles each function output on its own, so an output that
        # never reads an input must not evaluate it. The sky's horizon-hills mask feeds the chain
        # that ends in the same function's colour input: eager binding makes that a cycle.
        ins = {fi.get("ExpressionInputId"): _Lazy(g, fi.get("Input"), scope, self.section)
               for fi in p.get("FunctionInputs", [])}
        special = self.env.special_function(fname, _SpecialInputs(self, list(ins.values())))
        if special is not None:
            return special.get(out, self.const(0.0))
        found = self.function_graph(x)
        if found is None:
            self.warnings.append("no graph for function %s -> 0" % fname)
            return self.const(0.0)
        fg = found[1]
        # a texture can't cross a node-group socket: a function taking one
        # (Flowmaps_3D takes its noise volume) is inlined into its caller
        takes_texture = any("Texture" in str((x.get("Properties") or {}).get("InputType", ""))
                            for x in fg.inputs.values())
        if getattr(self.env, "nest_functions", False) and not takes_texture:
            return self.call_group(fname, fg, outs, out, ins, (id(x), g.path, scope.get("_id", 0)))
        FunctionsMixin._scope_id[0] += 1
        sub = dict(ins)
        sub["_id"] = (id(x), g.path, scope.get("_id", 0))
        fo = self.function_output(fg, outs, out)
        if fo is None:
            return self.const(0.0)
        saved = self.section
        self.section = saved + [section_name(fname)]
        try:
            return self.input(fg, fo["Properties"].get("A"), sub, self.const(0.0))
        finally:
            self.section = saved

    def _closes_loop(self, node, src):
        """Whether a link from `src` (an output socket) into `node` would close a loop: `node`
        already reaches src's node."""
        target = src.node.name
        nxt = {}
        for l in self.L:
            nxt.setdefault(l.from_node.name, []).append(l.to_node.name)
        seen, todo = {node.name}, [node.name]
        while todo:
            a = todo.pop()
            if a == target:
                return True
            for b in nxt.get(a, ()):
                if b not in seen:
                    seen.add(b)
                    todo.append(b)
        return False

    def fold_call(self, ft, fg, fo, name, w, ins, site):
        """A call whose output reads only inputs known when built (constants all the way): the
        function worked out inline, a value instead of a group node (a time of day's weights
        broken out of a float4 the file holds no day for). None where it isn't."""
        needs = ft.reached(name) | (ft.reached(socket_name(name + " (A)")) if w == 4 else set())
        if not needs:
            return None
        # in the group's input order, not the set's: reading an input builds the caller's nodes
        # (and other calls' outputs) as it goes, and the first non-constant one stops it; a set's
        # order followed PYTHONHASHSEED, and so did the trees
        needs = [s for s in ft.kind if s in needs]
        if any(ft.kind[s][0] not in ("ue", "ue_alpha") for s in needs):
            return None
        for sock in needs:
            kind, key = ft.kind[sock]
            lazy = ins.get(key)
            v = lazy.get(self) if lazy is not None else None
            if v is None and sock in ft.previews:
                v = self.input(fg, ft.previews[sock], {"_id": ("preview", ft.fname, sock)}, None)
            if v is None or not v.const or (kind == "ue_alpha" and not self.alpha(v).const):
                return None
        sub = dict(ins)
        sub["_id"] = ("fold", site)
        v = self.input(fg, fo["Properties"].get("A"), sub, self.const(0.0))
        if v.const and (v.w != 4 or v.a is None or v.a.const):
            return v
        # not a constant after all: what that made reaches nothing and goes when duplicates merge
        return None

    def call_group(self, fname, fg, outs, out, ins, site=None):
        fo = self.function_output(fg, outs, out)
        if fo is None:
            return self.const(0.0)
        statics = self.function_statics(fg, ins)
        # by graph: two functions can share a name in different folders; and by the parameters
        # the import fixes (folded into the function's body: env.function_key)
        fixed = self._hook("function_key", lambda: ())
        key = (fg.path, tuple(sorted(statics.items())), fixed) if statics or fixed else fg.path
        ft = self.functions.get(key)
        if ft is None:
            ft = self.functions[key] = FunctionTree(self, fname, fg, statics, self.variant_label(fname, fg, statics))
            if getattr(self.env, "whole_functions", False):
                # every output, in the function's own order: two materials calling it build the same
                # group and share it (built as each asked, their groups differed by what each read:
                # a copy of the function per material). Unread outputs cost nothing at render:
                # Blender drops what a material's output doesn't reach.
                for each in fg.outputs.values():
                    try:
                        ft.output(each)
                    except RuntimeError as e:       # (an output UE would compile on its own: a loop here)
                        self.warnings.append("%s: output %s left out (%s)" % (fname, (each.get("Properties") or {}).get("OutputName"), e))
        name, w = ft.output(fo)
        if w != "attrs":
            if name in ft.const_outs:
                return ft.const_outs[name]
            folded = self.fold_call(ft, fg, fo, name, w, ins, site)
            if folded is not None:
                return folded

        def fresh():
            n = self.node("ShaderNodeGroup", section_name(fname))
            n.node_tree = ft.tree
            return n, set()

        # One node per call, whichever outputs are read: UE compiles each output on its own, and
        # a node per output read drew the same function three times over (Pre FX's M, Distance
        # Blend and Pre Skinned Local Position). Each output still links only the inputs it
        # reaches, as it's read; one whose input would come from the node's own output (a loop
        # UE never sees) gets a node of its own instead.
        if w == "attrs" and not any(o[0] == "socket" for o in ft.attr_outs[fo["Properties"].get("Id")].values()):
            node, linked = None, set()
        else:
            had = self.call_sites.get(site) if site is not None else None
            if had is not None and had[0].node_tree is ft.tree:
                node, linked = had
                self.shared_calls.add(node.name)
            else:
                node, linked = fresh()
                if site is not None:
                    self.call_sites[site] = (node, linked)

        def link_needs(needs, node, linked):
            # the env learns which function a parameter is reached through (a
            # material's parameters can be grouped by it)
            caller = getattr(self.env, "caller", None)
            if self.function is None:
                self.env.caller = section_name(fname)
            try:
                for sock in sorted(needs - linked):
                    linked.add(sock)
                    link_one(sock, node, linked)
            finally:
                self.env.caller = caller

        def attach(needs, node, linked):
            """Link `needs` into the call's node, or into a node of their own where that loops."""
            try:
                link_needs(needs, node, linked)
                return node
            except _Loop:
                own, mine = fresh()
                link_needs(needs, own, mine)
                return own

        def link_one(sock, node, linked):
            kind, key = ft.kind[sock]
            if kind == "tex":
                # the texture's closure: the root's zone, or this function's own Closure input
                self.L.new(self.texture_closure(*ft.textures[sock]), node.inputs[sock])
                return
            if kind == "ue_attr":
                fid, attr = key
                lazy = ins.get(fid)
                v = lazy.get(self) if lazy is not None else None
                attrs = v.s if v is not None and isinstance(v.s, Attrs) else Attrs()
                v = attrs.get(attr)
            elif kind in ("ue", "ue_alpha"):
                lazy = ins.get(key)
                v = lazy.get(self) if lazy is not None else None
                if v is None and sock in ft.previews:
                    v = self.input(fg, ft.previews[sock], {"_id": ("preview", fname, sock)}, None)
                if v is not None and kind == "ue_alpha":
                    v = self.alpha(v)
            else:
                v = self.env.group_input(key)
            if v is None:
                return
            if node.name in self.shared_calls and not v.const and not isinstance(v.s, (Attrs, TexRef))                     and self._closes_loop(node, v.s):
                linked.discard(sock)
                raise _Loop()
            self.link(v, node.inputs[sock])

        if w == "attrs":
            # each attribute links only the inputs it reaches, when something reads it: UE compiles
            # a Material Attributes output one property at a time (InfoInvader's visor reads the
            # Normal of the face's attributes, whose Emissive needs the visor's UVs: linking every
            # attribute's inputs at once is a loop)
            souts = ft.attr_outs[fo["Properties"].get("Id")]
            passed = {}
            got = Attrs()
            for a, o in souts.items():
                if o[0] == "pass":
                    # unchanged by the function: the caller's own attribute,
                    # read only if something reads it
                    def own(fid=o[1], a=a):
                        if fid not in passed:
                            lazy = ins.get(fid)
                            passed[fid] = as_attrs(lazy.get(self) if lazy is not None else None)
                        return passed[fid].get(a)
                    got.thunks[a] = self.deferred(own)
                elif o[0] == "const":
                    got.vals[a] = o[1]
                else:
                    def made(sock=o[1], width=o[2]):
                        return Val(attach(ft.reached(sock), node, linked).outputs[sock], width)
                    got.thunks[a] = self.deferred(made)
            return Val(got, 0)
        at = attach(ft.reached(name) | ft.reached(socket_name(name + " (A)")) if w == 4 else ft.reached(name),
                    node, linked)
        if w == 4:
            return Val(at.outputs[name], 4, Val(at.outputs[socket_name(name + " (A)")], 1))
        return Val(at.outputs[name], w)

    def material_group(self, path, prop, name, finish=None):
        """A whole material's output as a node group (the sky's second slot)."""
        import bpy
        g = self.graph(path)
        key = ("material", path, prop)
        ft = self.functions.get(key)
        if ft is None:
            ft = self.functions[key] = FunctionTree(self, name, g)
            ft.tree.name = ft.prefix + name
            ft.tr.section = [name]
            prev = ft.tr.activate()
            try:
                v = ft.tr.input(g, g.editor_data()["Properties"][prop], {}, ft.tr.const(0.0))
                if finish is not None:
                    v = finish(ft.tr, v)
                ft.tree.interface.new_socket("Emissive Color", in_out='OUTPUT', socket_type='NodeSocketColor')
                if v.const:
                    v = ft.tr.solid(v)
                ft.tr.L.new(v.s, ft.go.inputs["Emissive Color"])
            finally:
                prev.activate()
            ft.outs[key] = ("Emissive Color", 3)
        node = self.node("ShaderNodeGroup", name)
        node.node_tree = ft.tree
        for sock in sorted(ft.reached("Emissive Color")):
            kind, k = ft.kind[sock]
            if kind == "env":
                self.link(self.env.group_input(k), node.inputs[sock])
            elif kind == "tex":
                self.L.new(self.texture_closure(*ft.textures[sock]), node.inputs[sock])
        return Val(node.outputs["Emissive Color"], 3)
