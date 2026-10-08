"""Static switches and parameters resolved when the graph is built."""

import math

from .core import (
    COMPILE_SWITCHES,
    FunctionTree,
    STATIC_BOOL_INPUT,
    T,
    TexKey,
    Val,
    LazyInput,
    inner_path,
    linked,
    object_path,
    section_name,
)


class StaticsMixin:
    def tex_key(self, prop):
        """A texture (or collection) reference as the env names it: the object
        path when it asks for paths (env.asset_paths), else the name."""
        if isinstance(prop, dict):
            name, path = inner_path(str(prop.get("ObjectName", ""))), str(prop.get("ObjectPath", ""))
        else:
            name, path = inner_path(str(prop or "")), ""
        if getattr(self.env, "asset_paths", False) and path:
            return object_path(path, name)
        return name

    def tex_param(self, p, key):
        """The texture a texture parameter holds: the env's (an instance override), else its
        default. The key remembers the parameter (TexKey.param) for the image node sampling it."""
        name = p.get("ParameterName")
        if name is None:
            return key
        got = self._hook("texture_parameter", lambda: key, name, key)
        return TexKey.of(got, name) if isinstance(got, str) else got

    def shared(self, key, make):
        """One node (or value) per tree for a Blender input read in many places."""
        if key not in self._shared:
            self._shared[key] = make()
        return self._shared[key]

    def static_switch(self, name, default):
        return bool(self._hook("static_switch", lambda: default, name, default))

    def static_bool(self, g, ref, scope):
        """What a static bool input compiles to, or None when nothing is linked
        or the expression isn't a compile-time value."""
        if not linked(ref):
            return None
        x = g.by.get(g.key(ref))
        if x is None:
            return None
        t, p = T(x), x.get("Properties") or {}
        if t in ("Reroute", "NamedRerouteDeclaration"):
            return self.static_bool(g, p.get("Input"), scope)
        if t == "NamedRerouteUsage":
            decl = self.declaration(g, p)
            return self.static_bool(g, decl["Properties"].get("Input"), scope) if decl else None
        if t == "StaticBool":
            return bool(p.get("Value", False))
        if t == "StaticBoolParameter":
            return self.static_switch(p.get("ParameterName"), bool(p.get("DefaultValue", False)))
        if t in ("StaticSwitch", "StaticSwitchParameter"):
            return self.static_bool(g, p.get("A" if self.switch_value(g, x, scope) else "B"), scope)
        if t in COMPILE_SWITCHES or t == "LandscapeLayerSwitch":
            return self.static_bool(g, self.switch_pin(x), scope)
        if t == "FunctionInput":
            return self.static_input(g, x, scope)
        if t == "MaterialFunctionCall":
            found = self.function_graph(x)
            if found is None:
                return None
            fg = found[1]
            fo = self.function_output(fg, p.get("FunctionOutputs", []), ref.get("OutputIndex", 0) or 0)
            if fo is None:
                return None
            sub = {fi.get("ExpressionInputId"): LazyInput(g, fi.get("Input"), scope, self.section)
                   for fi in p.get("FunctionInputs", [])}
            sub["_id"] = ("static", id(x), g.path, scope.get("_id", 0))
            return self.static_bool(fg, fo["Properties"].get("A"), sub)
        return None

    def static_input(self, g, x, scope):
        """A static bool FunctionInput: what the call site binds, else its preview."""
        p = x.get("Properties") or {}
        fid = p.get("Id")
        v = None
        fn = scope.get("_fn")
        if fn is not None:
            v = fn.statics.get(fid)
        else:
            bound = scope.get(fid)
            if isinstance(bound, LazyInput):
                v = self.static_bool(bound.g, bound.ref, bound.scope)
        if v is None:
            v = self.static_bool(g, p.get("Preview"), scope)
        if v is None:
            v = float((p.get("PreviewValue") or {}).get("X", 0.0)) != 0.0
        return v

    def switch_value(self, g, x, scope):
        """True for a StaticSwitch(Parameter)'s A branch."""
        p = x.get("Properties") or {}
        if T(x) == "StaticSwitchParameter":
            return self.static_switch(p.get("ParameterName"), bool(p.get("DefaultValue", False)))
        v = self.static_bool(g, p.get("Value"), scope)
        if v is None:
            if linked(p.get("Value")):
                self.warnings.append("static switch %s: value isn't static, took its default" % x.get("Name"))
            return bool(p.get("DefaultValue", False))
        return v

    def switch_pin(self, x):
        """The input a compile-time switch compiles (COMPILE_SWITCHES)."""
        p = x.get("Properties") or {}
        t = T(x)
        if t == "LandscapeLayerSwitch":
            used = self._hook("landscape_layer", lambda: True, p.get("ParameterName"))
            return p.get("LayerUsed" if used else "LayerNotUsed")
        prefs = COMPILE_SWITCHES[t]
        for k in prefs:
            if linked(p.get(k)):
                return p[k]
        return p.get(prefs[-1])

    def function_statics(self, fg, ins):
        """Each static bool input of a function at one call site: what the
        caller links (evaluated at compile time), else the input's preview."""
        out = {}
        for fid, fi in fg.inputs.items():
            fp = fi.get("Properties") or {}
            if fp.get("InputType") != STATIC_BOOL_INPUT:
                continue
            lazy = ins.get(fid)
            v = self.static_bool(lazy.g, lazy.ref, lazy.scope) if lazy is not None else None
            if v is None and lazy is not None and linked(lazy.ref):
                self.warnings.append("static input %s of %s isn't static, took its preview"
                                     % (fp.get("InputName"), fg.name))
            if v is None:
                v = self.static_bool(fg, fp.get("Preview"), {})
            if v is None:
                v = float((fp.get("PreviewValue") or {}).get("X", 0.0)) != 0.0
            out[fid] = v
        return out

    def variant_label(self, fname, fg, statics):
        """A function's group name for one set of static values: the plain
        name at its defaults, else the inputs that differ ("Foo [+UseX -Y]")."""
        base = section_name(fname)
        dflt = self.function_statics(fg, {}) if statics else {}
        diff = ["%s%s" % ("+" if v else "-", (fg.inputs[f].get("Properties") or {}).get("InputName", "?"))
                for f, v in sorted(statics.items(), key=lambda kv: str(kv[0])) if v != dflt.get(f)]
        label = "%s [%s]" % (base, " ".join(diff)) if diff else base
        prefix = getattr(self.env, "function_prefix", FunctionTree.PREFIX)
        taken = {ft.tree.name for ft in self.functions.values()}
        if not statics or not diff:
            # a plain function whose name another function already has
            n = 2
            while prefix + label in taken:
                label = "%s %d" % (base, n)
                n += 1
            return label
        if len((prefix + label).encode("utf-8")) > 63 or prefix + label in taken:
            n = 2
            while "%s%s [v%d]" % (prefix, base, n) in taken:
                n += 1
            label = "%s [v%d]" % (base, n)
        return label

    def evaluate_static(self, g, x, t, out, scope, p, P):
        if t in ("StaticSwitch", "StaticSwitchParameter"):
            pin = "A" if self.switch_value(g, x, scope) else "B"
            return self.input(g, p.get(pin), scope, self.const(0.0))
        if t in ("StaticBool", "StaticBoolParameter"):
            v = bool(p.get("Value", False)) if t == "StaticBool" else \
                self.static_switch(p.get("ParameterName"), bool(p.get("DefaultValue", False)))
            return self.const(1.0 if v else 0.0)
        if t in COMPILE_SWITCHES or t == "LandscapeLayerSwitch":
            return self.input(g, self.switch_pin(x), scope, self.const(0.0))
        if t == "StaticComponentMaskParameter":
            dflt = tuple(bool(p.get("Default" + c, False)) for c in "RGBA")
            name = p.get("ParameterName")
            v = self.input(g, p.get("Input"), scope, self.const(0.0))
            m = self._hook("static_mask", lambda: None, name, dflt)
            if m is None:
                # the instance doesn't say (a cooked instance keeps no mask choice, it's
                # editor-only data): the env may tell from the texture behind it, else the default
                m = self._hook("guess_mask", lambda: dflt, name, dflt, v)
            idx = [i for i, on in enumerate(m) if on]
            return self.mask(v, idx) if idx else self.const(0.0)
        if t == "ChannelMaskParameter":
            # a vector parameter (one-hot by default) dotted with the input
            ch = str(p.get("MaskChannel", "Red")).split("::")[-1]
            i = {"Red": 0, "Green": 1, "Blue": 2, "Alpha": 3}.get(ch, 0)
            rgb, a = self.env.vector(p.get("ParameterName"), tuple(1.0 if k == i else 0.0 for k in range(4)))
            v = self.input(g, p.get("Input"), scope, self.const(0.0))
            v3 = self.combine([v, v, v]) if v.w == 1 else self.zero_z(Val(v.s, min(v.w, 3)))
            d = self.vmath('DOT_PRODUCT', v3, rgb)
            if v.w in (1, 4):
                d = self.binop('ADD', d, self.binop('MULTIPLY', self.alpha(v), a))
            return d
        if t == "Switch":
            # floor(SwitchValue) picks an input; out of range, Default
            sv = self.input(g, p.get("SwitchValue"), scope, self.const(float(p.get("ConstSwitchValue", 0.0))))
            items = p.get("Inputs") or []
            if sv.const:
                # the input picked is known: neither the others nor Default (unless picked) are built
                i = int(math.floor(sv.s if sv.w == 1 else sv.s[0]))
                if 0 <= i < len(items) and linked(items[i].get("Input")):
                    return self.input(g, items[i].get("Input"), scope, None)
            result = self.input(g, p.get("Default"), scope, self.const(float(p.get("ConstDefault", 0.0))))
            if sv.const:
                return result
            idx = self.math('FLOOR', self.mask(sv, [0]))
            for i, it in enumerate(items):
                v = self.input(g, it.get("Input"), scope, None)
                if v is not None:
                    hit = self.math('COMPARE', idx, self.const(float(i)), self.const(0.5))
                    result = self.lerp(result, v, hit, label="switch %d" % i)
            return result
        return None

    # ---------------------------------------------------- attributes
    def deferred(self, fn):
        """fn, to run later in this translator and the frame it'd run in now
        (an attribute's nodes are made when something reads it)."""
        section = list(self.section)

        def run():
            prev = self.activate()
            saved = self.section
            self.section = section
            try:
                return fn()
            finally:
                self.section = saved
                prev.activate()
        return run
