using System.Collections.Generic;
using CUE4Parse.UE4.Objects.Core.Math;
using FortnitePorting.CUE4Parse.Models.Fortnite;
using FortnitePorting.CUE4Parse.Models.Fortnite.Enums;

namespace FortnitePorting.Exporting.Models;

public class BaseMeta
{
    public string? DeformRig;     // MP: the character's deform Control Rig dump (deform_rig)
    public string? AnimBlueprint;     // MP: the part's anim blueprint dump (dynamic bones)
    public string? PhysicsAsset;     // MP: its RigidBody node's physics asset dump (dynamic bones)
    public Dictionary<string, string>? PhysicsAssets;     // MP: each RigidBody node's physics asset dump, by node
    public string? DynamicsParameters;     // MP: the per-state dynamics parameters its anim blueprint reads
    public string? AnimScript;     // MP: its anim blueprint with bytecode (the values bound to the dynamics nodes)
}

public class ExportMasterSkeletonMeta : BaseMeta
{
    public ExportMesh MasterSkeletalMesh;
}

public class ExportPoseAssetMeta : BaseMeta
{
    public string PoseAsset;
}

public class ExportAttachMeta : BaseMeta
{
    public bool AttachToSocket;
    public string? Socket;
}

public class ExportHatMeta : ExportAttachMeta
{
    public string HatType;
}

public class ExportHeadMeta : ExportPoseAssetMeta
{
    public readonly Dictionary<ECustomHatType, string> MorphNames = new();
    public FLinearColor SkinColor;
    public string? FaceDNA;     // MP: the head's RigLogic data (riglogic_face)
}