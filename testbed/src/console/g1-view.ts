import * as THREE from 'three';
import type { G1Mesh, G1Part, G1Pose } from './g1-body.ts';

/**
 * G1 渲染适配器：一个可视 geom 一个节点，位姿每帧从 MuJoCo 的
 * geom_xpos / geom_xmat 读；几何直接建自模型里的 mesh_vert / mesh_face。
 *
 * 为什么不用 STLLoader：MuJoCo 编译时会把网格顶点居中（mesh_pos），
 * 再用 geom_pos 补回去。另读一份原始 STL 会把偏移算两遍，头会飘在身子上方 0.4 m。
 */
export class G1View {
  /** MuJoCo 是 z 上、three 是 y 上：整组绕 x 转 -90° 对齐 */
  readonly group = new THREE.Group();
  private readonly nodes = new Map<number, THREE.Object3D>();
  private readonly container = new THREE.Group();
  private readonly m4 = new THREE.Matrix4();
  private height = 0;

  private constructor() {
    this.container.rotation.x = -Math.PI / 2;
    this.group.add(this.container);
    this.group.visible = false;
  }

  static build(parts: G1Part[], meshes: G1Mesh[]): G1View {
    const view = new G1View();
    const light = new THREE.MeshStandardMaterial({ color: 0xc9d2dc, metalness: 0.55, roughness: 0.38 });
    const dark = new THREE.MeshStandardMaterial({ color: 0x242a33, metalness: 0.6, roughness: 0.45 });

    const byName = new Map<string, THREE.BufferGeometry>();
    for (const mesh of meshes) {
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.BufferAttribute(mesh.vert, 3));
      g.setIndex(new THREE.BufferAttribute(new Uint32Array(mesh.face), 1));
      g.computeVertexNormals();
      g.computeBoundingSphere();
      byName.set(mesh.name, g);
    }

    let triangles = 0;
    for (const p of parts) {
      const geo = byName.get(p.mesh);
      if (!geo) continue;
      triangles += geo.index ? geo.index.count / 3 : 0;
      const node = new THREE.Object3D();
      node.userData = { mesh: p.mesh, geomId: p.geomId };
      view.nodes.set(p.geomId, node);
      view.container.add(node);
      const isDark = /rubber|contour|logo/i.test(p.mesh);
      const object = new THREE.Mesh(geo, isDark ? dark : light);
      object.castShadow = true;
      object.receiveShadow = false;
      node.add(object);
    }
    (view as unknown as { triangles: number }).triangles = triangles;
    return view;
  }

  /** 用当前姿态量模型高度；单位明显不对（mm/inch）才缩放 */
  calibrate(pose: G1Pose): number {
    this.sync(pose);
    this.group.updateMatrixWorld(true);
    const box = new THREE.Box3().setFromObject(this.container);
    const size = box.getSize(new THREE.Vector3());
    this.height = size.y;
    if (this.height > 0 && (this.height < 0.6 || this.height > 3)) {
      this.container.scale.setScalar(1.3 / this.height);
      this.height = 1.3;
    }
    return this.height;
  }

  get modelHeight(): number {
    return this.height;
  }

  get triangleCount(): number {
    return (this as unknown as { triangles: number }).triangles ?? 0;
  }

  sync(pose: G1Pose): void {
    const m4 = this.m4;
    for (const [geomId, node] of this.nodes) {
      if (geomId >= pose.ngeom) continue;
      const p = geomId * 3;
      const r = geomId * 9;
      node.position.set(Number(pose.geomXpos[p]), Number(pose.geomXpos[p + 1]), Number(pose.geomXpos[p + 2]));
      m4.set(
        Number(pose.geomXmat[r]), Number(pose.geomXmat[r + 1]), Number(pose.geomXmat[r + 2]), 0,
        Number(pose.geomXmat[r + 3]), Number(pose.geomXmat[r + 4]), Number(pose.geomXmat[r + 5]), 0,
        Number(pose.geomXmat[r + 6]), Number(pose.geomXmat[r + 7]), Number(pose.geomXmat[r + 8]), 0,
        0, 0, 0, 1,
      );
      node.quaternion.setFromRotationMatrix(m4);
    }
  }
}
