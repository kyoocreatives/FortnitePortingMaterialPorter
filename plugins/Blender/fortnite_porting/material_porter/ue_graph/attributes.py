"""Material Attributes: make, set, get, blend, break."""

from .core import (
    ATTRIBUTES_INPUT,
    Attrs,
    BREAK_ORDER,
    BlendAttrs,
    COMPILE_SWITCHES,
    MAKE_PINS,
    MapAttrs,
    SHADING_MODELS,
    T,
    VERTEX_ATTRIBUTES,
    Val,
    _Lazy,
    _Statics,
    as_attrs,
    attribute_default,
    attribute_name,
    linked,
)


class AttributesMixin:
    @staticmethod
    def attribute_value(v, name):
        """An attribute's value. UE compiles Material Attributes one property
        at a time, so Material Attributes wired into an attribute's pin give
        their own attribute of that name."""
        return v.s.get(name) if isinstance(v.s, Attrs) else v

    def passthrough(self, g, ref, scope):
        """{attribute: input id} for the attributes a Material Attributes value carries
        unchanged from one of its function's Material Attributes inputs (through Set
        nodes that don't touch them, reroutes, switches). Worked out without making nodes."""
        if not linked(ref):
            return {}
        x = g.by.get(g.key(ref))
        if x is None:
            return {}
        t, p = T(x), x.get("Properties") or {}
        if t in ("Reroute", "NamedRerouteDeclaration"):
            return self.passthrough(g, p.get("Input"), scope)
        if t == "NamedRerouteUsage":
            decl = self.declaration(g, p)
            return self.passthrough(g, decl["Properties"].get("Input"), scope) if decl else {}
        if t in ("StaticSwitch", "StaticSwitchParameter"):
            return self.passthrough(g, p.get("A" if self.switch_value(g, x, scope) else "B"), scope)
        if t in COMPILE_SWITCHES:
            return self.passthrough(g, self.switch_pin(x), scope)
        if t == "FunctionInput" and p.get("InputType") == ATTRIBUTES_INPUT and scope.get("_fn") is not None:
            return {a: p.get("Id") for a in self.carried}
        if t == "GetMaterialAttributes" and not (ref.get("OutputIndex") or 0):
            return self.passthrough(g, p.get("MaterialAttributes"), scope)
        if t == "SetMaterialAttributes":
            ins = p.get("Inputs") or []
            base = self.passthrough(g, ins[0], scope) if ins else {}
            for k, guid in enumerate(p.get("AttributeSetTypes") or []):
                r = ins[k + 1] if k + 1 < len(ins) else None
                if linked(r):
                    name = attribute_name(guid, r.get("InputName"))
                    if self.attribute_source(g, r, scope, name) != base.get(name):
                        base.pop(name, None)
            return base
        if t == "BlendMaterialAttributes":
            # UseA / UseB keep one side's attribute; a blend of the same
            # attribute with itself is that attribute
            pa, pb = self.passthrough(g, p.get("A"), scope), self.passthrough(g, p.get("B"), scope)
            mode = lambda k: str(p.get(k, "EMaterialAttributeBlend::Blend")).split("::")[-1]
            out = {}
            for a in self.carried:
                m = mode("VertexAttributeBlendType" if a in VERTEX_ATTRIBUTES else "PixelAttributeBlendType")
                src = pa.get(a) if m == "UseA" else pb.get(a) if m == "UseB" else \
                    (pa.get(a) if pa.get(a) is not None and pa.get(a) == pb.get(a) else None)
                if src is not None:
                    out[a] = src
            return out
        if t == "MaterialFunctionCall":
            # through a function: what it passes from its own inputs, mapped
            # back to what this call links there
            found = self.function_graph(x)
            if found is None:
                return {}
            fg = found[1]
            fo = self.function_output(fg, p.get("FunctionOutputs", []), ref.get("OutputIndex") or 0)
            if fo is None:
                return {}
            ins = {fi.get("ExpressionInputId"): _Lazy(g, fi.get("Input"), scope, self.section)
                   for fi in p.get("FunctionInputs", [])}
            inner = {"_fn": _Statics(self.function_statics(fg, ins)), "_id": ("pass", id(x))}
            out, mine = {}, {}
            for a, fid in self.passthrough(fg, fo["Properties"].get("A"), inner).items():
                lazy = ins.get(fid)
                if lazy is None or not linked(lazy.ref):
                    continue
                if fid not in mine:
                    mine[fid] = self.passthrough(g, lazy.ref, scope)
                if a in mine[fid]:
                    out[a] = mine[fid][a]
            return out
        if t == "MakeMaterialAttributes":
            # Make(Break(input)...): an attribute wired back to its own pin
            out = {}
            for pin, r in p.items():
                a = MAKE_PINS.get(pin)
                if a in self.carried and linked(r):
                    src = self.attribute_source(g, r, scope, a)
                    if src is not None:
                        out[a] = src
            return out
        return {}

    def attribute_source(self, g, ref, scope, name):
        """The input id whose attribute `name` a value is, unchanged (a Break
        or Get output of it, through reroutes), or None."""
        if not linked(ref):
            return None
        x = g.by.get(g.key(ref))
        if x is None:
            return None
        t, p = T(x), x.get("Properties") or {}
        out = ref.get("OutputIndex") or 0
        if t in ("Reroute", "NamedRerouteDeclaration"):
            return self.attribute_source(g, p.get("Input"), scope, name)
        if t == "NamedRerouteUsage":
            decl = self.declaration(g, p)
            return self.attribute_source(g, decl["Properties"].get("Input"), scope, name) if decl else None
        if t == "BreakMaterialAttributes" and out < len(BREAK_ORDER) and BREAK_ORDER[out] == name:
            return self.passthrough(g, p.get("MaterialAttributes"), scope).get(name)
        if t == "GetMaterialAttributes" and out > 0:
            types = p.get("AttributeGetTypes") or []
            outs = p.get("Outputs") or []
            label = outs[out].get("OutputName") if out < len(outs) and isinstance(outs[out], dict) else None
            if out - 1 < len(types) and attribute_name(types[out - 1], label) == name:
                return self.passthrough(g, p.get("MaterialAttributes"), scope).get(name)
        return None

    def attrs(self, g, ref, scope):
        """A Material Attributes input as Attrs (UE's defaults when unlinked)."""
        v = self.input(g, ref, scope, None)
        return v.s if v is not None and isinstance(v.s, Attrs) else Attrs()

    def evaluate_attributes(self, g, x, t, out, scope, p, P):
        if t == "MakeMaterialAttributes":
            thunks = {}
            for pin, ref in p.items():
                name = MAKE_PINS.get(pin)
                if name and linked(ref):
                    thunks[name] = self.deferred(lambda ref=ref, name=name: self.attribute_value(self.input(
                        g, ref, scope, attribute_default(name)), name))
            return Val(Attrs(None, thunks), 0)
        if t == "SetMaterialAttributes":
            ins = p.get("Inputs") or []
            base = self.attrs(g, ins[0], scope) if ins else Attrs()
            thunks = {}
            for k, guid in enumerate(p.get("AttributeSetTypes") or []):
                ref = ins[k + 1] if k + 1 < len(ins) else None
                if linked(ref):
                    name = attribute_name(guid, ref.get("InputName"))
                    thunks[name] = self.deferred(lambda ref=ref, name=name: self.attribute_value(self.input(
                        g, ref, scope, attribute_default(name)), name))
            return Val(Attrs(base, thunks), 0)
        if t == "GetMaterialAttributes":
            a = self.attrs(g, p.get("MaterialAttributes"), scope)
            if out == 0:
                return Val(a, 0)
            types = p.get("AttributeGetTypes") or []
            if out - 1 >= len(types):
                return self.const(0.0)
            outs = p.get("Outputs") or []
            label = outs[out].get("OutputName") if out < len(outs) and isinstance(outs[out], dict) else None
            return a.get(attribute_name(types[out - 1], label))
        if t == "BreakMaterialAttributes":
            a = self.attrs(g, p.get("MaterialAttributes"), scope)
            return a.get(BREAK_ORDER[out]) if out < len(BREAK_ORDER) else self.const(0.0)
        if t == "BlendMaterialAttributes":
            a = self.attrs(g, p.get("A"), scope)
            b = self.attrs(g, p.get("B"), scope)
            alpha = []

            def get_alpha():
                if not alpha:
                    alpha.append(self.mask(self.input(g, p.get("Alpha"), scope, self.const(0.0)), [0]))
                return alpha[0]
            mode = lambda k: str(p.get(k, "EMaterialAttributeBlend::Blend")).split("::")[-1]
            return Val(BlendAttrs(self, a, b, self.deferred(get_alpha),
                                  mode("PixelAttributeBlendType"), mode("VertexAttributeBlendType")), 0)
        if t == "ShadingModel":
            sm = str(p.get("ShadingModel", "EMaterialShadingModel::MSM_DefaultLit")).split("::")[-1]
            return self.const(float(SHADING_MODELS.index(sm)) if sm in SHADING_MODELS else 1.0)
        return None

    # ------------------------------------------------------------- maths
    def select(self, cond, if_false, if_true, label="if"):
        """cond (0 or 1) ? if_true : if_false."""
        if cond.const:
            return if_true if cond.s else if_false
        return self.lerp(if_false, if_true, cond, label=label)

    def _attrs_op(self, fn, *vals):
        """fn over Material Attributes, attribute by attribute."""
        return Val(MapAttrs(self, fn, [as_attrs(v) for v in vals]), 0)
