# Dynamic Bones 2 (RigidBody) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Fortnite's RigidBody (RBAN) chains baked in Blender with the engine's Chaos immediate solver, in graph order
with the AnimDynamics nodes and the Constraint / ModifyBone nodes that follow.

**Architecture:** Readers turn the anim blueprint and physics asset dumps into plain node data stored on the armature;
pure solver modules (bodies, joints, contacts, step, node) replay the engine; the existing bake drives every node kind
through one `evaluate(dt, cs, root)` interface.

**Tech Stack:** Blender 5 Python (mathutils), headless test runner (`rigcheck.sh`), devtools real check.

**Spec:** `docs/superpowers/specs/2026-10-10-rigid-body-design.md`

## Global Constraints

- No Epic code in the public fork: our own words and structure; formulas from the owner notes
  (`fpfork-private/devtools/helpers/rigidbody_algorithm.md`) and the source facts listed per task.
- Game numbers inside solvers: cm, kg, s, the game's axes, mathutils quaternions; solver body state in CoM frames.
- Files under ~900 lines; comments short; headless isolated Blender only.
- Executor note: this plan names formulas instead of pasting full code because the executing session read the engine
  source for them; every task still goes RED then GREEN.

## Review Focus

1. A physics asset whose bones the armature lacks (or a joint between two kinematic bodies): skipped, no error.
2. Reset frame: output equals input; a body never simulated is never keyed.
3. Joint frame order: the solver's body 0 is the parent (`ConstraintBone2`), body 1 the child.
4. Soft all-limited angular joints take the SIMD path (Jacobi within the joint, and angular projection applies).
5. Long bakes stay finite (no NaN from zero-length axes, zero inertia or zero mass).

---

### Task 1: Readers

**Files:** `processing/context/physics_read.py` (exists, spike), `processing/context/dynamics_read.py`,
`tests/plugin/rigid_check.py` (new suite).

- Tests first: synthetic physics dump (two capsules, one constraint with soft cone/twist, a drive, a disable pair,
  SolverSettings) → bodies by name in table order, defaults (soft 50/5, drive 50/1, damping 0.01/0, projection 5 cm /
  1.0 / 0.0), disabled pair names; mutate the reader (drop a default) to see each RED.
- `physics_read.asset` adds `solver` (position/velocity/projection iterations 6/1/1, cull 3, fixed step 0,
  `linear_solver` true) from `SolverSettings`.
- `dynamics_read`: every node gets `kind`; `rigid_body` nodes carry `space`, `base_bone`, `gravity` (override or None),
  `sim_space` block, `component_acc`, `external_force`, `use_default_as_simulated`, `alpha`, and
  `physics` (`physics_read.asset(path)` from the part's `PhysicsAsset` meta, passed in by `nodes(paths, physics)`);
  `constraint` nodes (bone, setups: target, offset option, type, per-axis, weights) and `modify_bone` nodes (bone,
  translation, rotation (pitch,yaw,roll), scale, modes, spaces). Unknown skeletal controls: one log line.

### Task 2: Bodies (`processing/context/rigid_bodies.py`)

- Capsule: segment `center ± axis·L/2`, axis = rotator · Z; volume `π r² (L + 4r/3)`; `I⊥ = m(5L³+20L²r+45Lr²+32r³)/(60L+80r)`,
  `I∥ = m r²(15L+16r)/(30L+40r)`. Sphere `0.4 m r²`. Box `m(b²+c²)/12`. Density 0.001 kg/cm³.
- Combine shapes (parallel axis), diagonalise (keep frame when off-diagonal energy < 1e-8·trace², else eigen); mass
  `max(MassScale · m^0.75, 0.001)` or override; inertia scaled by `m/m_raw`; COM nudge (+ `m·n²` per axis).
- Inertia conditioning (linear solver): extents E = max(local AABB half extents in CoM frame, |connector| per axis of
  joints not all-Free); valid if invM > 1e-20, min invI > 1e-4, min E > 1e-8; `r = invI/invM`; RX = E.x²(0,r.y,r.z),
  RY, RZ; ratio = max; Mrot = 2·min(1, max(E)/10); invI *= min(1, Mrot/ratio).
- Tests: worked example (r 1.5, L 6 → raw 0.0565, m 0.116, I⊥ ≈ 0.69, I∥ ≈ 0.124), override, nudge, conditioning in
  0.3–0.45 for a chain capsule.

### Task 3: Joints (`processing/context/rigid_joints.py`)

Source facts (FPBDJointCachedSolver, UE 5.8):
- Body 0 = parent with frame (Pos2, Pri2, Sec2), body 1 = child (Pos1, Pri1, Sec1); frames to CoM space; rotation
  `Pri, Sec, Pri×Sec` columns.
- Init per step: connectors from start pose (`X,R`) and predicted (`P,Q`); child rotation shortest-arc to parent's;
  `WDt_i = angvel(Rinit_i, Rc_i, 1)`; mass conditioning (inertia ratio ≤ 5 by lerp remap, parent mass and max inertia
  ≥ 0.2 of child's); world inverse inertia `R diag R^T` from Q; tolerance scale `min(1, 3600 dt²)`, pos 0.025, ang 0.001.
- Linear locked (SIMD when all three locked): axes = parent connector columns, CX_k = (C1−C0)·a_k, flip axis when CX<0,
  arm0 = C1 − P0 − Σ a_k·CX_k (raw), arm1 = C1 − P1, IM_k = invM0 + invM1 + (arm×a)·InvI(arm×a) per body; solve all three
  from the same DP/DQ; DP0 += invM0 a λ, DQ0 += IA0 λ, DP1 −= invM1 a λ, DQ1 −= IA1 λ.
  Limited (spherical/cylindrical/planar) rows: arms both from C1, limit check, tolerance skip, soft (×1.5 / ×1.2).
- Angular: R01 = Rc0⁻¹ Rc1; twist T = normalize(x,0,0,w) (identity if x = 0), swing S = R01 T⁻¹; twist angle
  2·acos(T.w) wrapped to (−π, π], sign of T.x, axis Rc1·X; R0S = Rc0·S; swing1 axis R0S·Z angle 4·atan2(S.z, 1+S.w);
  swing2 axis R0S·Y angle 4·atan2(S.y, 1+S.w); degenerate when (Rc0·X)·(Rc1·X) < −0.998 (skip twist and single-swing
  rows). Rows store the flipped axis and |angle|; limit check: excess beyond ±limit, skip within AngTol.
  Pyramid when both swings limited; dual cone when one limited and the other free (axis normalize((Rc0·other)×(Rc1·X)),
  angle asin(−dot)); locked axes from the closed-form locked axes with the quaternion component as error, no limit check.
  SIMD (all three limited and twist+swing soft): Jacobi over twist, swing2, swing1 from the same DQ; else sequential
  index order 0 twist, 1 swing2, 2 swing1. Soft: Ks = k dt²/IM, Kd = c dt/IM (k = 1e5·S, c = 1e3·D),
  SoftIM = (Ks+Kd)·IM + 1, Δλ = (Ks e − Kd vdt − λ)/SoftIM with vdt = a·((DQ0+WDt0) − (DQ1+WDt1)); hard Δλ = e/IM.
  DQ0 += IA0 λ, DQ1 −= IA1 λ.
- Order per position iteration: rotation rows, position rows, rotation drives, position drives.
- Drives: SLERP (no locked angular axis): target identity (or OrientationTarget), R1Error = target⁻¹·R01 (target made
  shortest-arc to R01), errors 2·asin5(R1Error.xyz) (5th-order series), axes = Rc1 columns; k = 1.5·S, c = 1.5·D, same
  soft formula with CX = error + a·(DQ1−DQ0); applied with the axis projection `DQ += a (a·IA) Δλ`; Jacobi (SIMD) by
  default; max torque clamp when MaxForce > 0. Twist/swing mode: R1Error = (Rc0·target)⁻¹ Rc1, twist 2·R1Error.x,
  swing1 = (R1Error·X).y, swing2 = −(R1Error·X).z, only rows past tolerance or damped.
- Velocity phase: hard rows with |λ| > 1e-8 (SIMD linear: all three if any); angular before linear; restitution
  targets only on limited linear rows. Soft rows: none.
- Projection (child dynamic, enabled): re-init with corrected poses, parent invM = invI = 0, child invI = diag(min
  invIL), lambdas kept; iteration 0 teleport (|CX| > 5 cm on hard position rows: DP1 −= a·CX); rotation projection only
  if angular alpha > 0 (SIMD: every row; scalar: hard rows), DR1 = α·(−IA1)·Δλ with Δλ = e/(a·IA1), and with all-locked
  linear DP1 −= α·(DR1 × arm1); position projection α = linear alpha: IM = invM1 + (arm1×a)·IA1, DP1 −= α invM1 a Δλ,
  DQ1 −= α IA1 Δλ (SIMD jointly); last iteration V1 += 0.1·DP1/dt, W1 += 0.1·DQ1/dt (the joint's own body deltas).
- Sort: by level (BFS from joints touching a kinematic body) then insertion order.
- Tests: pin under gravity; soft swing 94% per pass at 60 Hz (50, 5); pyramid independence; twist sign; projection
  teleport and 10% velocity; SLERP drive pulls toward identity; mass conditioning 0.2.

### Task 4: Contacts (`processing/context/rigid_contacts.py`)

- Pairs: all body pairs with at least one dynamic, minus disabled table and joint pairs with `disable_collision`;
  capsule/sphere closest points of segments; φ = dist − r0 − r1; keep if φ < 3 (cull); one point per pair; box as its
  bounding capsule.
- Position (after joints in each iteration): e = φ + n·(DP0 − DP1) + DQ0·(r0×n) − DQ1·(r1×n) with n from 1 to 0; if e < 0 or
  net push-out > 0: p = −e/K, net push-out ≥ 0 clamp; apply. Velocity: pushed contacts drive normal velocity to
  restitution 0.3 target (only when approach speed > 1.5·|g|·dt), net impulse ≥ 0, friction 0.7 in the last iteration.
- Tests: overlapping capsules separate along the normal; a disabled pair doesn't; resting contact under gravity keeps
  a capsule on another within 0.1 cm.

### Task 5: Step and node (`processing/context/rigid_solver.py`)

- Step (one sub-step h): integrate dynamics (DV = gravity·h + forces, sim-space terms when WorldAlpha > 0, V =
  lerp(V+DV, TargetV, min(1, linDamp·h)), same for W; P = X + V h; Q = integrate(R, W, h)); kinematic targets with the
  (step+1)/N fraction quirk; gather (kinematic X = P − V h); joints init (sorted); contacts; 6 position iterations
  (joints then contacts); V += DP/h, W += DQ/h; 1 velocity iteration; apply corrections (P += DP, Q = integrate(Q, DQ, 1));
  projection pass; scatter (P += DP, Q = integrate(Q, DQ, 1)); X, R = P, Q.
- Node `RigidSim(node, parent_of)`: `bones()` = body bones + base bone; `evaluate(dt, cs, root)`: spaces
  (Component/World identity, BaseBone relative to the base bone's cs), first call = reset (bodies at input, V = W = 0,
  history S1 = S2 = S, PreviousDt = 1/30, step time = min(dt, 1/30); output = input); later: kinematic targets, gravity
  into the space (world vector, base rot⁻¹), sim-space motion (§1.6 of the notes), step time rolling average (N = 100),
  sub-steps; write simulated bodies only (actor frame from CoM frame).
- Tests: reset frame identity; a hanging capsule under gravity settles; WorldAlpha 1 base motion swings a chain, 0
  doesn't; sub-step fractions 1/3, 7/9 for N = 3; step time averaging.

### Task 6: Bone controls (`processing/context/bone_controls.py`)

- Constraint node (FAnimNode_Constraint) and ModifyBone (FAnimNode_ModifyBone) as the engine evaluates them; read
  `AnimNode_Constraint.cpp` and `AnimNode_ModifyBone.cpp` for the exact blending before coding.
- Same `evaluate(dt, cs, root)` interface; `bones()` lists target and modified bones.
- Tests per mode/space against hand-computed transforms.

### Task 7: Bake and import

- `dynamics_bake`: node factory by `kind` (`Sim`, `RigidSim`, `BoneControl`); simulated set = every bone a node
  returns; rest unchanged (twins, keys, toggle).
- `mesh_hooks._dynamics`: pass `PhysicsAsset` metas into `dynamics_read.nodes`; skeleton keeps nodes whose bones exist
  (rigid nodes: the asset filtered to existing bones).
- Tests: synthetic head capsule + 3-link hair chain bakes, keys on twins, the chain stays out of the head capsule.

### Task 8: Real check

- `fpfork-private/devtools/dynamics_real.py` covers rigid nodes: VibrantShell FaceAcc (hair locks, tail, necklace,
  earrings) + body (bow, boba) with FingerGuns; joints within 0.5 cm, limits + 2°, no NaN, time logged.
- Then review, commit wording, publish on request.
