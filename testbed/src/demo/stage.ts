import * as THREE from 'three';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { G1View } from '../console/g1-view.ts';
import type { MjPose } from '../console/g1-mesh.ts';
import type { RayHit, RoomBox } from './clearance.ts';
import { DemoCameraRig, type CameraState } from './camera.ts';

// 演示台的 3D 部分：只做「摆场景 + 每帧同步」。
//
// 不重复写渲染代码：G1 的 35 块网格仍然由 console 的 buildG1WsView()/G1View 建（见 main.ts），
// 本文件只把那个 view 挂进来、每帧 sync(pose)。房间的盒子来自 sim/env/scene_room.json
// （serve-demo.mjs 原样转发），不是另抄一份坐标——画出来的墙/桌子就是 MuJoCo 里挡人的那一批。
//
// 深色是为了跟右侧面板一致：墨色底 + 冷灰机器人，没有渐变堆叠。

const COL = {
  bg: 0x0b0f14,
  structure: 0x1e2833,
  furniture: 0x27323f,
  edge: 0x3a4a5b,
  floor: 0x1c2631,
  gridA: 0x33465a,
  gridB: 0x233142,
  ray: 0x7c9cff,
  contact: 0xd99a5b,
};

/** 射线扇形（应与 clearance.ts 的 FAN_DEG 同长度；这里只是线段缓冲尺寸） */
const FAN = 7;

export class DemoStage {
  readonly renderer: THREE.WebGLRenderer;
  readonly scene = new THREE.Scene();
  readonly camera: THREE.PerspectiveCamera;

  private readonly canvas: HTMLCanvasElement;
  private view: G1View | null = null;

  private readonly rayGeom: THREE.BufferGeometry;
  private readonly rayLines: THREE.LineSegments;
  private raysOn = false;

  private readonly ringA: THREE.Mesh;
  private readonly ringB: THREE.Mesh;
  private contactsOn = false;

  private readonly focusPoint = new THREE.Vector3(0, 0.9, 0);
  private readonly camOffset = new THREE.Vector3(2.15, 1.5, 3.1);

  /** 摄像机两态（锁定 = 跟随默认机位 / 解锁 = 自由移动）。逻辑在 camera.ts，这里只做接线 */
  readonly cameraRig: DemoCameraRig;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.05;

    this.scene.background = new THREE.Color(COL.bg);
    this.scene.fog = new THREE.Fog(COL.bg, 9, 18);

    this.camera = new THREE.PerspectiveCamera(36, 1, 0.05, 60);
    this.camera.position.copy(this.focusPoint).add(this.camOffset);
    this.camera.lookAt(this.focusPoint);

    // 摄像机两态：默认锁定（就是上面这个默认机位），点舞台右上角的图标切到自由态
    this.cameraRig = new DemoCameraRig({
      camera: this.camera,
      canvas,
      focus: this.focusPoint,
      defaultOffset: this.camOffset,
    });

    // PBR 需要有环境光才不是死黑：RoomEnvironment 只当光源用，强度压到 0.35
    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.environmentIntensity = 0.45;

    this.scene.add(new THREE.HemisphereLight(0xdce8f5, 0x151c24, 0.55));
    // 补光：从机位一侧压一点冷光，盒子才有体积，不然全糊成一片黑
    const fill = new THREE.DirectionalLight(0x9fb6d0, 0.45);
    fill.position.set(-3.5, 2.4, 2.2);
    this.scene.add(fill);
    const key = new THREE.DirectionalLight(0xf2f7ff, 1.7);
    key.position.set(3.2, 6.2, 3.8);
    key.castShadow = true;
    key.shadow.mapSize.set(2048, 2048);
    key.shadow.camera.left = -6;
    key.shadow.camera.right = 6;
    key.shadow.camera.top = 6;
    key.shadow.camera.bottom = -6;
    key.shadow.camera.near = 0.5;
    key.shadow.camera.far = 20;
    key.shadow.bias = -0.0008;
    this.scene.add(key);

    // 射线：7 条线段，端点每帧（10 Hz）重写。打中东西的用强调色画到碰撞点，
    // 没打中的只画一根 0.3 m 短桩（暗色）—— 不然整个地面都是线，看不出来哪边是空的。
    this.rayGeom = new THREE.BufferGeometry();
    this.rayGeom.setAttribute('position', new THREE.BufferAttribute(new Float32Array(FAN * 2 * 3), 3));
    this.rayGeom.setAttribute('color', new THREE.BufferAttribute(new Float32Array(FAN * 2 * 3), 3));
    this.rayLines = new THREE.LineSegments(
      this.rayGeom,
      new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.95, depthWrite: false }),
    );
    this.rayLines.frustumCulled = false;
    this.rayLines.visible = false;
    this.scene.add(this.rayLines);

    const ringGeo = new THREE.RingGeometry(0.055, 0.082, 24);
    const ringMat = () =>
      new THREE.MeshBasicMaterial({
        color: COL.contact,
        transparent: true,
        opacity: 0.9,
        side: THREE.DoubleSide,
        depthWrite: false,
      });
    this.ringA = new THREE.Mesh(ringGeo, ringMat());
    this.ringB = new THREE.Mesh(ringGeo, ringMat());
    for (const ring of [this.ringA, this.ringB]) {
      ring.rotation.x = -Math.PI / 2;
      ring.visible = false;
      ring.renderOrder = 2;
      this.scene.add(ring);
    }
  }

  /** 房间：盒子直接来自 scene_room.json（structure = 墙/地板，furniture = 家具） */
  installRoom(boxes: RoomBox[]): void {
    for (const b of boxes) {
      if (b.collider === 'plane') {
        // 地板单独画成网格面：盒子画出来会是一个厚板，脚会埋进去
        const floor = new THREE.Mesh(
          new THREE.PlaneGeometry(b.hx * 2, b.hz * 2),
          new THREE.MeshStandardMaterial({ color: COL.floor, roughness: 0.96, metalness: 0 }),
        );
        floor.rotation.x = -Math.PI / 2;
        floor.position.set(b.cx, b.cy + b.hy, b.cz);
        floor.receiveShadow = true;
        this.scene.add(floor);
        const grid = new THREE.GridHelper(b.hx * 2, Math.round(b.hx * 4), COL.gridA, COL.gridB);
        grid.position.set(b.cx, b.cy + b.hy + 0.002, b.cz);
        (grid.material as THREE.Material).transparent = true;
        (grid.material as THREE.Material).opacity = 0.85;
        this.scene.add(grid);
        continue;
      }
      const geo = new THREE.BoxGeometry(b.hx * 2, b.hy * 2, b.hz * 2);
      const mesh = new THREE.Mesh(
        geo,
        new THREE.MeshStandardMaterial({
          color: b.kind === 'structure' ? COL.structure : COL.furniture,
          roughness: 0.92,
          metalness: 0.04,
        }),
      );
      mesh.position.set(b.cx, b.cy, b.cz);
      mesh.castShadow = b.kind !== 'structure';
      mesh.receiveShadow = true;
      this.scene.add(mesh);

      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geo),
        new THREE.LineBasicMaterial({ color: COL.edge, transparent: true, opacity: 0.5 }),
      );
      edges.position.copy(mesh.position);
      this.scene.add(edges);
    }
  }

  /** 挂上 console 那份 G1View（几何/位姿适配器都在 console 里，这里不重写） */
  attachG1(view: G1View): void {
    this.view = view;
    view.group.visible = true;
    this.scene.add(view.group);
  }

  get hasG1(): boolean {
    return this.view !== null;
  }

  /** 跟随点的时间常数（s）：60 fps 下每帧 ≈0.12，与旧行为同手感；掉帧时按墙钟收敛，不会"慢放" */
  private static readonly FOLLOW_TAU = 0.13;

  /** 每帧：把最近一帧服务端位姿灌进 G1View（pose 为 null 时保持上一帧，不编数据） */
  syncPose(pose: MjPose | null, x: number, z: number, dt = 0.016): void {
    if (this.view && pose) this.view.sync(pose);
    // 跟随机位用墙钟 dt（不是夹到 50 ms 的物理 dt）：低帧率下镜头跟随不该被拖成慢动作
    const k = 1 - Math.exp(-Math.max(0, dt) / DemoStage.FOLLOW_TAU);
    this.focusPoint.x += (x - this.focusPoint.x) * k;
    this.focusPoint.z += (z - this.focusPoint.z) * k;
  }

  setRaysVisible(on: boolean): void {
    this.raysOn = on;
    this.rayLines.visible = on;
  }

  /**
   * 射线：原点在机器人底座正下方的地面上，长度 = 到盒子的距离。
   * 没打中 / 超出量程的只画一小截短桩（暗色），打中的画到碰撞点（强调色）。
   */
  updateRays(ox: number, oz: number, hits: RayHit[], maxDist = 3): void {
    if (!this.raysOn) return;
    const pos = this.rayGeom.getAttribute('position') as THREE.BufferAttribute;
    const col = this.rayGeom.getAttribute('color') as THREE.BufferAttribute;
    const y = 0.03;
    const stub = 0.3;
    const hitCol = [0.62, 0.75, 1.0] as const;
    const missCol = [0.24, 0.31, 0.4] as const;
    for (let i = 0; i < FAN; i++) {
      const h = hits[i];
      const k = i * 6;
      const blocked = !!h && h.dist < maxDist - 1e-3;
      const len = blocked ? h.dist : stub;
      pos.setXYZ(k, ox, y, oz);
      pos.setXYZ(k + 1, ox + (h ? h.dx : 1) * len, y, oz + (h ? h.dz : 0) * len);
      const c = blocked ? hitCol : missCol;
      col.setXYZ(k, c[0], c[1], c[2]);
      col.setXYZ(k + 1, c[0], c[1], c[2]);
    }
    pos.needsUpdate = true;
    col.needsUpdate = true;
    this.rayGeom.computeBoundingSphere();
  }

  setContactsVisible(on: boolean): void {
    this.contactsOn = on;
    if (!on) {
      this.ringA.visible = false;
      this.ringB.visible = false;
    }
  }

  /**
   * 接触：服务端只给 ncon 计数，不给接触点坐标。所以圆环画在**左右踝 geom 的真实位置**
   * （geom.xpos，three 系）——它是"脚在哪儿"的真值，不是"接触点"的坐标，别混。
   * ncon == 0 时压暗，让人一眼看出"这一帧没接触"。
   */
  updateContacts(points: [number, number, number][], ncon: number): void {
    if (!this.contactsOn) return;
    const rings = [this.ringA, this.ringB];
    const opacity = ncon > 0 ? 0.95 : 0.22;
    for (let i = 0; i < rings.length; i++) {
      const ring = rings[i];
      const p = points[i];
      if (!p) {
        ring.visible = false;
        continue;
      }
      ring.visible = true;
      ring.position.set(p[0], 0.014, p[2]);
      (ring.material as THREE.MeshBasicMaterial).opacity = opacity;
    }
  }

  resize(): void {
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
  }

  render(dt = 0.016): void {
    // 摄像机位姿完全交给 cameraRig（锁定 = 跟随默认机位 / 解锁 = 自由移动）。
    // 跟随点已经在 syncPose 里平滑过，这里不再叠一层 lerp，免得两套平滑互相打架。
    this.cameraRig.update(dt);
    this.renderer.render(this.scene, this.camera);
  }

  /** 关键帧/调试用：立刻把相机挪回默认机位（截图等不了过渡） */
  snapCamera(): void {
    this.cameraRig.snapDefault();
  }

  /** 摄像机只读快照（__demo.camera 探针 / demo-check --camera 用） */
  cameraState(): CameraState {
    return this.cameraRig.state();
  }
}
