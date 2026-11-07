"""S-loco spike: run DeepMind mujoco_playground G1 joystick ONNX policy on OUR g1.xml.

Not part of sim_server runtime. Read-only wrt existing sim_server code (new file only).
Policy: rss2025/mujoco_playground/experimental/sim2sim/onnx/g1_policy.onnx (903,543 B)
Obs layout copied from the repo's play_g1_joystick.py, NOT guessed:
  [local_linvel(3), gyro(3), gravity(3), command(3), qpos[7:]-default(29),
   qvel[6:](29), last_action(29), cos(phase)(2), sin(phase)(2)] = 103  == onnx input
"""
from __future__ import annotations
import sys, math
from pathlib import Path
import numpy as np
import mujoco
import onnxruntime as rt

REPO = Path(__file__).resolve().parents[2]
G1_XML = REPO / "testbed" / "public" / "mujoco" / "g1" / "g1.xml"
ONNX = REPO / ".tmp" / "loco" / "g1mp" / "g1_policy.onnx"

# policy joint order = actuator order of playground g1_mjx_feetonly.xml (verified by grep)
ORDER = [
    "left_hip_pitch_joint","left_hip_roll_joint","left_hip_yaw_joint","left_knee_joint",
    "left_ankle_pitch_joint","left_ankle_roll_joint",
    "right_hip_pitch_joint","right_hip_roll_joint","right_hip_yaw_joint","right_knee_joint",
    "right_ankle_pitch_joint","right_ankle_roll_joint",
    "waist_yaw_joint","waist_roll_joint","waist_pitch_joint",
    "left_shoulder_pitch_joint","left_shoulder_roll_joint","left_shoulder_yaw_joint",
    "left_elbow_joint","left_wrist_roll_joint","left_wrist_pitch_joint","left_wrist_yaw_joint",
    "right_shoulder_pitch_joint","right_shoulder_roll_joint","right_shoulder_yaw_joint",
    "right_elbow_joint","right_wrist_roll_joint","right_wrist_pitch_joint","right_wrist_yaw_joint",
]
# keyframe "knees_bent" qpos[7:] from scene_mjx_feetonly_flat_terrain.xml (verbatim)
DEFAULT = np.array([
    -0.312,0,0,0.669,-0.363,0,
    -0.312,0,0,0.669,-0.363,0,
    0,0,0.073,
    0.2,0.2,0,0.6,0,0,0,
    0.2,-0.2,0,0.6,0,0,0,
], dtype=np.float64)

ACTION_SCALE = 0.5      # play_g1_joystick.py: action_scale=0.5
CTRL_DT = 0.02
SUBSTEPS = 10
GAIT_FREQ = 1.5
START_Z = 0.755         # keyframe knees_bent pelvis z


def build():
    spec = mujoco.MjSpec.from_file(str(G1_XML))
    f = spec.worldbody.add_geom(); f.name = "floor"
    f.type = mujoco.mjtGeom.mjGEOM_PLANE; f.size = [0.0, 0.0, 0.05]
    m = spec.compile(); m.opt.timestep = 0.002
    return m


def index_map(m):
    """policy index -> qpos index / actuator index, resolved by joint name."""
    jname = {}
    for j in range(m.njnt):
        jname[j] = mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_JOINT, j)
    act_of_joint = {}
    for a in range(m.nu):
        jid = int(m.actuator_trnid[a, 0])
        act_of_joint[jname[jid]] = a
    qpos_of, act_of, jidx = [], [], []
    for n in ORDER:
        jid = [j for j in range(m.njnt) if jname[j] == n][0]
        jidx.append(jid)
        qpos_of.append(int(m.jnt_qposadr[jid]))
        act_of.append(act_of_joint[n])
    return np.array(qpos_of), np.array(act_of), np.array(jidx)


def set_kp(m, kp):
    if kp is None:
        return
    m.actuator_gainprm[:, 0] = kp
    m.actuator_biasprm[:, 1] = -kp


def run(kp=None, kd=None, seconds=5.0, cmd=(0.5, 0.0, 0.0), tag=""):
    m = build(); set_kp(m, kp)
    if kd is not None:
        m.actuator_biasprm[:, 2] = -kd
    d = mujoco.MjData(m)
    qpos_of, act_of, jidx = index_map(m)

    # init to knees_bent pose (policy's own reset keyframe)
    d.qpos[:] = 0.0; d.qpos[2] = START_Z; d.qpos[3] = 1.0
    d.qpos[qpos_of] = DEFAULT
    d.ctrl[:] = DEFAULT[None, :][0][range(29)] if False else 0.0
    d.ctrl[act_of] = DEFAULT
    mujoco.mj_forward(m, d)

    sess = rt.InferenceSession(str(ONNX), providers=["CPUExecutionProvider"])
    last_action = np.zeros(29, dtype=np.float32)
    phase = np.array([0.0, math.pi])
    phase_dt = 2 * math.pi * GAIT_FREQ * CTRL_DT

    pelvis = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
    R = np.zeros(9); v6 = np.zeros(6)
    n_ctrl = int(round(seconds / CTRL_DT))
    speeds, zs, steps = [], [], 0
    qmin = m.jnt_range[:, 0].copy(); qmax = m.jnt_range[:, 1].copy()
    viol = 0

    for _ in range(n_ctrl):
        # ---- obs (same order as play_g1_joystick.get_obs) ----
        mujoco.mju_quat2Mat(R, d.qpos[3:7]); Rm = R.reshape(3, 3)
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_BODY, pelvis, v6, 1)  # local
        linvel = v6[3:6].copy()
        gyro = v6[0:3].copy()
        gravity = Rm.T @ np.array([0.0, 0.0, -1.0])
        js = d.qpos[qpos_of] - DEFAULT
        jv = d.qvel[np.array(qpos_of) - 7 + 6]
        ph = np.concatenate([np.cos(phase), np.sin(phase)])
        obs = np.hstack([linvel, gyro, gravity, np.array(cmd), js, jv, last_action, ph]).astype(np.float32)
        assert obs.shape == (103,), obs.shape
        act = sess.run(["continuous_actions"], {"obs": obs.reshape(1, -1)})[0][0]
        last_action = act.copy()
        d.ctrl[act_of] = act * ACTION_SCALE + DEFAULT
        phase = np.fmod(phase + phase_dt + np.pi, 2 * np.pi) - np.pi

        for _ in range(SUBSTEPS):
            mujoco.mj_step(m, d)
            steps += 1
            if not np.isfinite(d.qpos).all():
                return dict(tag=tag, nan=True, t=steps * 0.002)
        speeds.append(float(np.hypot(d.qvel[0], d.qvel[1])))
        zs.append(float(d.qpos[2]))
        q = d.qpos[qpos_of]
        viol = max(viol, int(np.sum((q < qmin[jidx] - 1e-6) | (q > qmax[jidx] + 1e-6))))

    x0y0 = (0.0, 0.0)
    xy = np.hypot(d.qpos[0], d.qpos[1])
    tail = speeds[len(speeds)//5:]  # ignore first 20% (transient)
    return dict(tag=tag, kp=kp, kd=kd,
                mean_speed_tail=float(np.mean(tail)), final_speed=speeds[-1],
                avg_speed_all=float(np.mean(speeds)),
                x_final=float(d.qpos[0]), y_final=float(d.qpos[1]),
                dist=float(xy), mean_cmd_speed=float(xy / seconds),
                z_min=float(np.min(zs)), z_final=float(zs[-1]),
                yaw_final=float(math.atan2(2*(d.qpos[3]*d.qpos[4]+d.qpos[5]*d.qpos[6]),
                                           1-2*(d.qpos[5]**2+d.qpos[6]**2))),
                fell=bool(min(zs) < 0.45), joint_limit_violations=viol,
                nan=False, steps=steps)


if __name__ == "__main__":
    out = []
    out.append(run(75.0, 2.0, 5.0, (0.5, 0, 0), "B5s: kp=75 kd=2, cmd(0.5,0,0), 5 s"))
    out.append(run(75.0, 2.0, 30.0, (0.5, 0, 0), "B30s: kp=75 kd=2, cmd(0.5,0,0), 30 s"))
    out.append(run(75.0, 2.0, 5.0, (0.0, 0, 0), "C: stand, cmd(0,0,0), 5 s -> drift"))
    out.append(run(75.0, 2.0, 5.0, (0.0, 0, 0.5), "D: turn, cmd(0,0,0.5 rad/s), 5 s"))
    out.append(run(None, None, 5.0, (0.5, 0, 0), "A: our g1.xml as-is (kp=500 dampratio=1)"))
    for r in out:
        print(r)
