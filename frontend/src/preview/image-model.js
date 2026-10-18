import * as THREE from '../../vendor/three.module.min.js';
import { GLTFLoader } from '../../vendor/GLTFLoader.js';

// Image-to-3D services must return a self-contained GLB. No model-provided
// resource URL may send the browser to another service or private address.
export async function parseImageModel(data) {
  if (!(data instanceof ArrayBuffer) || data.byteLength < 20 || data.byteLength > 50 * 1024 * 1024)
    throw new Error('Invalid or oversized 3D model file.');
  const header = new DataView(data);
  if (header.getUint32(0, true) !== 0x46546c67 || header.getUint32(4, true) !== 2 || header.getUint32(8, true) !== data.byteLength || header.getUint32(16, true) !== 0x4e4f534a)
    throw new Error('3D model must be a GLB version 2 file.');
  const length = header.getUint32(12, true);
  if (length > data.byteLength - 20) throw new Error('Incomplete 3D model file.');
  const manifest = JSON.parse(new TextDecoder().decode(new Uint8Array(data, 20, length)));
  if ([...(manifest.buffers || []), ...(manifest.images || [])].some(item => item.uri))
    throw new Error('3D model must contain textures and geometry in a single file.');
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(url => {
    if (!url.startsWith('blob:')) throw new Error('Additional 3D model resources were rejected.');
    return url;
  });
  const gltf = await new GLTFLoader(manager).parseAsync(data, '');
  const root = gltf.scene;
  let meshes = 0;
  root.traverse(object => {
    if (object.isMesh) meshes++;
    // Model lighting and cameras do not override SISA's viewer.
    if (object.isLight || object.isCamera) object.visible = false;
  });
  const bounds = new THREE.Box3().setFromObject(root);
  const size = bounds.getSize(new THREE.Vector3());
  if (!meshes || ![size.x, size.y, size.z].every(Number.isFinite) || Math.max(size.x, size.y, size.z) <= 0) {
    disposeImageModel(root);
    throw new Error('File does not contain a renderable 3D object.');
  }
  return { root, bounds, size };
}

export function disposeImageModel(root) {
  const materials = new Set(), textures = new Set(), geometries = new Set();
  root.traverse(object => {
    if (object.geometry) geometries.add(object.geometry);
    if (object.material) for (const material of [].concat(object.material)) materials.add(material);
  });
  for (const material of materials) {
    for (const value of Object.values(material)) if (value?.isTexture) textures.add(value);
    material.dispose();
  }
  for (const texture of textures) {
    texture.source?.data?.close?.();
    texture.dispose();
  }
  for (const geometry of geometries) geometry.dispose();
}
