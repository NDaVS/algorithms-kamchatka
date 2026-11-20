import * as THREE from './vendor/three.module.min.js';
import { $, request } from './api.js';

class SnowSurface {
  id = 'snow-surface';
  type = 'custom';
  renderingMode = '3d';

  onAdd(map, gl) {
    this.map = map;
    this.scene = new THREE.Scene();
    this.camera = new THREE.Camera();
    this.renderer = new THREE.WebGLRenderer({ canvas: map.getCanvas(), context: gl });
    this.renderer.autoClear = false;
    this.scene.add(new THREE.HemisphereLight('#eff8ff', '#57748a', 2.1));
    const light = new THREE.DirectionalLight('#ffffff', 2);
    light.position.set(-1, -1, 2);
    this.scene.add(light);
  }

  clear() {
    if (this.mesh) {
      this.scene.remove(this.mesh);
      this.mesh.geometry.dispose();
      this.mesh.material.dispose();
      this.mesh = null;
    }
    this.map.triggerRepaint();
  }

  setData(data, scale, opacity) {
    this.clear();
    const { longitudes: xs, latitudes: ys, snow_cm: snow, ground_m: ground } = data;
    const origin = maplibregl.MercatorCoordinate.fromLngLat([xs[0], ys[0]]);
    this.origin = new THREE.Matrix4().makeTranslation(origin.x, origin.y, 0);
    const vertices = [], shades = [], triangles = [];
    const ids = new Int32Array(xs.length * ys.length).fill(-1);
    const max = snow.reduce((v, row) => row.reduce((m, n) => Math.max(m, n ?? 0), v), 0);
    const light = new THREE.Color('#edf6fa'), deep = new THREE.Color('#85bfe8');
    const terrain = Boolean(this.map.getTerrain());
    for (let j = 0; j < ys.length; j++) {
      for (let i = 0; i < xs.length; i++) {
        const value = snow[j][i];
        if (value == null || value <= .01) continue;
        const coords = [xs[i], ys[j]];
        const base = terrain ? ground[j][i] : 0;
        const p = maplibregl.MercatorCoordinate.fromLngLat(coords, base + Math.max(.06, value*.01*scale));
        ids[j * xs.length + i] = vertices.length / 3;
        vertices.push(p.x-origin.x, p.y-origin.y, p.z);
        const color = light.clone().lerp(deep, value/Math.max(max, 1));
        shades.push(color.r, color.g, color.b);
      }
    }
    function triangle(...corners) {
      if (corners.every(i => i >= 0)) triangles.push(...corners);
    }
    for (let j = 0; j < ys.length-1; j++) {
      for (let i = 0; i < xs.length-1; i++) {
        const k = j*xs.length+i;
        const a = ids[k], b = ids[k+1], c = ids[k+xs.length], d = ids[k+xs.length+1];
        if (b < 0 || c < 0) {
          triangle(a, b, d);
          triangle(a, d, c);
        } else {
          triangle(a, b, c);
          triangle(b, d, c);
        }
      }
    }
    if (!triangles.length) return;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(shades, 3));
    geometry.setIndex(triangles);
    geometry.computeVertexNormals();
    this.mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
      vertexColors: true, side: THREE.DoubleSide, roughness: .9, transparent: true, opacity
    }));
    this.mesh.frustumCulled = false;
    this.scene.add(this.mesh);
    this.map.triggerRepaint();
  }

  render(gl, frame) {
    if (!this.mesh) return;
    this.camera.projectionMatrix.fromArray(frame.defaultProjectionData.mainMatrix).multiply(this.origin);
    this.renderer.resetState();
    this.renderer.render(this.scene, this.camera);
  }

  onRemove() {
    this.clear();
    this.renderer.dispose();
  }
}

export function createSnowVolume(map) {
  const layer = new SnowSurface();
  map.addLayer(layer, 'network');
  let key;
  let pending = 0;
  let payload;
  const draw = () => {
    if (!payload) return;
    layer.setData(payload, Number($('snow-height').value), $('ops-snow-opacity').valueAsNumber/100);
  };
  for (const id of ['snow-height', 'ops-snow-opacity', 'terrain']) $(id).addEventListener('change', draw);
  return {
    show(runId, frame) {
      const next = runId ? `${runId}:${frame ?? ''}` : null;
      if (next === key) return;
      key = next;
      const token = ++pending;
      if (!key) {
        payload = undefined;
        layer.clear();
        return;
      }
      const suffix = Number.isInteger(frame) ? `?frame=${frame}` : '';
      request(`/operations/city/runs/${runId}/surface${suffix}`).then(data => {
        if (token !== pending) return;
        payload = data;
        draw();
      }).catch(error => {
        if (token !== pending) return;
        payload = undefined;
        layer.clear();
        $('ops-result-status').textContent = error.message;
      });
    }
  };
}
