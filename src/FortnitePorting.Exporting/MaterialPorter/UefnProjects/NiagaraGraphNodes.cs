using System.Runtime.CompilerServices;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.EdGraph;

namespace FortnitePorting.Exporting.MaterialPorter;

/// <summary>An uncooked Niagara asset's graph nodes read as graph nodes, with their pins: what feeds each module input (a
/// dynamic input, a curve) is only in the pins' links.</summary>
public static class NiagaraGraphNodes
{
    static readonly string[] Classes =
    [
        "NiagaraNodeFunctionCall", "NiagaraNodeAssignment", "NiagaraNodeCustomHlsl", "NiagaraNodeInput", "NiagaraNodeOutput",
        "NiagaraNodeParameterMapSet", "NiagaraNodeParameterMapGet", "NiagaraNodeEmitter", "NiagaraNodeOp", "NiagaraNodeReroute",
        "NiagaraNodeIf", "NiagaraNodeSelect", "NiagaraNodeStaticSwitch", "NiagaraNodeConvert", "NiagaraNodeReadDataSet",
        "NiagaraNodeWriteDataSet", "NiagaraNodeDataSetRead", "NiagaraNodeUsageSelector", "NiagaraNodeSimTargetSelector",
    ];

    [ModuleInitializer]
    internal static void Register()
    {
        foreach (var name in Classes) ObjectTypeRegistry.RegisterClass(name, typeof(UEdGraphNode));
    }
}
