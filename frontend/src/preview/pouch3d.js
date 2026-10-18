import * as THREE from "../../vendor/three.module.min.js";
import { OrbitControls } from "../../vendor/OrbitControls.js";

// Dimensions and panel colors are measured inputs. Depth is cosmetic only.
export function createPouch(container, input, fallback) {
  const {
    W_mm: W,
    H_mm: H,
    front,
    back,
    variant,
    hem_mm,
    hem_stitch_mm,
  } = input;
  const test =
    new URLSearchParams(location.search).has("test") ||
    new URLSearchParams(location.search).has("dev");
  let renderer;
  try {
    if (new URLSearchParams(location.search).has("webglOff"))
      throw new Error("disabled");
    renderer = new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      preserveDrawingBuffer: test,
    });
  } catch {
    container.innerHTML =
      '<p class="caption">3D preview is not available on this device.</p>' +
      fallback;
    return {
      dispose() {
        container.innerHTML = "";
      },
      setVisible() {},
      preset() {},
      dimensions() {},
      seam() {},
      lock() {},
      async loadModel() { throw new Error('This device does not support viewing 3D objects.'); },
      restorePattern() {},
    };
  }
  container.innerHTML = "";
  container.append(renderer.domElement);
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.05;
  const scene = new THREE.Scene(),
    camera = new THREE.PerspectiveCamera(36, 1, 1, 2000),
    group = new THREE.Group();
  scene.add(group);
  scene.add(new THREE.HemisphereLight(0xfff9ed, 0x7a7e8a, 2.4));
  const light = new THREE.DirectionalLight(0xffffff, 2.3);
  light.position.set(-150, 300, 300);
  scene.add(light);
  const weave = document.createElement("canvas");
  weave.width = weave.height = 128;
  const ctx = weave.getContext("2d");
  ctx.fillStyle = "#eeeeee";
  ctx.fillRect(0, 0, 128, 128);
  for (let i = 0; i < 128; i += 8) {
    ctx.fillStyle = "#fafafa";
    ctx.fillRect(i, 0, 2, 128);
    ctx.fillStyle = "#dddddd";
    ctx.fillRect(0, i, 128, 1);
  }
  const map = new THREE.CanvasTexture(weave);
  map.wrapS = map.wrapT = THREE.RepeatWrapping;
  map.repeat.set(W / 12, H / 12);
  map.colorSpace = THREE.SRGBColorSpace;
  const normals = document.createElement("canvas");
  normals.width = normals.height = 128;
  const nc = normals.getContext("2d");
  nc.fillStyle = "#8080ff";
  nc.fillRect(0, 0, 128, 128);
  for (let i = 0; i < 128; i += 8) {
    nc.fillStyle = "#7080ee";
    nc.fillRect(i, 0, 2, 128);
    nc.fillStyle = "#8090ee";
    nc.fillRect(0, i, 128, 2);
  }
  const normalMap = new THREE.CanvasTexture(normals);
  normalMap.wrapS = normalMap.wrapT = THREE.RepeatWrapping;
  normalMap.repeat.copy(map.repeat);
  const materials = [];
  function cloth(hex, darken = 1) {
    const m = new THREE.MeshStandardMaterial({
      color: new THREE.Color(hex).multiplyScalar(darken),
      map,
      normalMap,
      normalScale: new THREE.Vector2(0.08, 0.08),
      roughness: 0.9,
      side: THREE.DoubleSide,
    });
    materials.push(m);
    return m;
  }
  // A cosmetic open shape, not a gusset or an estimate of interior capacity.
  // Both faces meet at the side/bottom seams; the free top forms a lens opening.
  const depth = Math.min(W * 0.28, H * 0.38);
  const thickness = 0.65;
  function zAt(x, y, side) {
    const across = Math.max(0, Math.cos(Math.PI * x / W));
    const up = Math.max(0, Math.min(1, y / H + 0.5));
    return side * (thickness + depth / 2 * Math.pow(across, 0.7) * Math.sin(up * Math.PI / 2));
  }
  function panel(
    x0,
    x1,
    side,
    hex,
    y0 = -H / 2,
    y1 = H / 2,
    darken = 1,
    offset = 0,
  ) {
    const geometry = new THREE.PlaneGeometry(x1 - x0, y1 - y0, 48, 32),
      pos = geometry.attributes.position;
    const inside = offset * side < 0;
    const shades = [];
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i) + (x0 + x1) / 2,
        y = pos.getY(i) + (y0 + y1) / 2;
      pos.setXYZ(i, x, y, zAt(x, y, side) + offset);
      if (inside) {
        // Cosmetic cavity shading: light enters through the open hem.
        const shade = 0.18 + 0.72 * Math.pow(y / H + 0.5, 2);
        shades.push(shade, shade, shade);
      }
    }
    // Back panels must face outwards for correct lighting.
    if (side === -1) {
      const indices = geometry.index.array;
      for (let i = 0; i < indices.length; i += 3) {
        [indices[i + 1], indices[i + 2]] = [indices[i + 2], indices[i + 1]];
      }
    }
    geometry.computeVertexNormals();
    const material = cloth(hex, darken);
    if (inside) {
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(shades, 3));
      material.vertexColors = true;
    }
    group.add(new THREE.Mesh(geometry, material));
  }
  const halves = variant === "front_split_center";
  front.forEach((face, i) => {
    const x0 = halves ? (i === 0 ? -W / 2 : 0) : -W / 2,
      x1 = halves ? (i === 0 ? 0 : W / 2) : W / 2;
    panel(x0, x1, 1, face.hex);
    panel(x0, x1, 1, face.hex, H / 2 - hem_mm, H / 2, 0.72, 0.35);
  });
  panel(-W / 2, W / 2, -1, back.hex);
  panel(-W / 2, W / 2, -1, back.hex, H / 2 - hem_mm, H / 2, 0.72, -0.35);
  // The inside uses each panel's own fabric, without inventing a lining.
  front.forEach((face, i) => {
    const x0 = halves ? (i === 0 ? -W / 2 : 0) : -W / 2,
      x1 = halves ? (i === 0 ? 0 : W / 2) : W / 2;
    panel(x0, x1, 1, face.hex, -H / 2, H / 2, 0.64, -thickness);
  });
  panel(-W / 2, W / 2, -1, back.hex, -H / 2, H / 2, 0.64, thickness);
  // Close only fabric thickness at the seams, leaving the top open.
  for (const x of [-W / 2, W / 2]) {
    const side = new THREE.Mesh(
      new THREE.PlaneGeometry(thickness * 2, H),
      cloth(back.hex, 0.75),
    );
    side.rotation.y = Math.PI / 2;
    side.position.x = x;
    group.add(side);
  }
  const bottom = new THREE.Mesh(
    new THREE.PlaneGeometry(W, thickness * 2),
    cloth(back.hex, 0.72),
  );
  bottom.rotation.x = Math.PI / 2;
  bottom.position.y = -H / 2;
  group.add(bottom);
  // Rolled top edges follow the same curve as the cloth faces.
  function rim(x0, x1, side, hex) {
    const points = Array.from({ length: 65 }, (_, i) => {
      const x = x0 + (x1 - x0) * i / 64;
      return new THREE.Vector3(x, H / 2, zAt(x, H / 2, side));
    });
    group.add(new THREE.Mesh(
      new THREE.TubeGeometry(new THREE.CatmullRomCurve3(points), 64, thickness * 0.7, 6, false),
      cloth(hex, 0.8),
    ));
  }
  front.forEach((face, i) => rim(halves && i === 1 ? 0 : -W / 2, halves && i === 0 ? 0 : W / 2, 1, face.hex));
  rim(-W / 2, W / 2, -1, back.hex);
  function line(points, dashed = false) {
    const geometry = new THREE.BufferGeometry().setFromPoints(
        points.map((p) => new THREE.Vector3(...p)),
      ),
      mat = dashed
        ? new THREE.LineDashedMaterial({
            color: 0xe9ddc5,
            dashSize: 1.3,
            gapSize: 1.3,
          })
        : new THREE.LineBasicMaterial({ color: 0xe9ddc5 });
    materials.push(mat);
    const l = new THREE.Line(geometry, mat);
    l.computeLineDistances();
    group.add(l);
  }
  function stitchPath(points, side) {
    const curved = [];
    for (let i = 1; i < points.length; i++) {
      const [x0, y0] = points[i - 1], [x1, y1] = points[i];
      for (let step = 0; step <= 48; step++) {
        const t = step / 48, x = x0 + (x1 - x0) * t, y = y0 + (y1 - y0) * t;
        curved.push([x, y, zAt(x, y, side) + side * 0.5]);
      }
    }
    line(curved, true);
  }
  for (const side of [1, -1]) {
    const top = H / 2 - hem_stitch_mm;
    stitchPath(
      [
        [-W / 2 + 2, top],
        [W / 2 - 2, top],
      ],
      side,
    );
    stitchPath(
      [
        [-W / 2 + 2, H / 2 - hem_mm],
        [-W / 2 + 2, -H / 2 + 2],
        [W / 2 - 2, -H / 2 + 2],
        [W / 2 - 2, H / 2 - hem_mm],
      ],
      side,
    );
  }
  if (halves)
    stitchPath(
      [
        [0, -H / 2],
        [0, H / 2 - hem_mm],
      ],
      1,
    );
  const shadowCanvas = document.createElement("canvas");
  shadowCanvas.width = shadowCanvas.height = 128;
  const sc = shadowCanvas.getContext("2d");
  sc.fillStyle = "rgba(30,36,48,.12)";
  sc.beginPath();
  sc.ellipse(64, 64, 58, 46, 0, 0, Math.PI * 2);
  sc.fill();
  const shadowMap = new THREE.CanvasTexture(shadowCanvas),
    shadowMaterial = new THREE.MeshBasicMaterial({
      map: shadowMap,
      transparent: true,
      depthWrite: false,
    }),
    shadow = new THREE.Mesh(
      new THREE.PlaneGeometry(W * 1.8, 70),
      shadowMaterial,
    );
  shadow.rotation.x = -Math.PI / 2;
  shadow.position.y = -H / 2 - 0.6;
  scene.add(shadow);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.enablePan = false;
  controls.minPolarAngle = 0.09;
  controls.maxPolarAngle = Math.PI * 0.78;
  const distance = Math.max(W, H) * 1.85;
  controls.minDistance = distance * 0.65;
  controls.maxDistance = distance * 1.5;
  controls.autoRotate = false;
  controls.autoRotateSpeed = 0.6;
  controls.target.set(0, 0, 0);
  let visible = true,
    onscreen = true,
    contextLost = false,
    drawCount = 0,
    previousFrame = 0,
    disposed = false,
    frame = 0,
    current = "threeQuarter",
    locked = false,
    dimensionsShown = false;
  let imageModel = null, modelLoadVersion = 0, disposeModel = null;
  const label = document.createElement("div");
  label.className = "dimension-label";
  label.hidden = true;
  label.textContent =
    `width ${W / 10} cm · height ${H / 10} cm` +
    (halves ? " · extra seam at the center" : "");
  container.append(label);
  const dimensionGroup = new THREE.Group();
  scene.add(dimensionGroup);
  dimensionGroup.visible = false;
  for (const points of [
    [
      [-W / 2, -H / 2 - 12, 6],
      [W / 2, -H / 2 - 12, 6],
    ],
    [
      [-W / 2 - 12, -H / 2, 6],
      [-W / 2 - 12, H / 2, 6],
    ],
  ]) {
    const geometry = new THREE.BufferGeometry().setFromPoints(
      points.map((p) => new THREE.Vector3(...p)),
    );
    dimensionGroup.add(
      new THREE.Line(
        geometry,
        new THREE.LineBasicMaterial({ color: 0x28418f }),
      ),
    );
  }
  const seamLabel = document.createElement("div");
  seamLabel.className = "seam-label";
  seamLabel.textContent = "Extra seam at the center";
  seamLabel.hidden = !halves;
  container.append(seamLabel);
  function preset(name) {
    current = name === "reset" ? "threeQuarter" : name;
    controls.autoRotate = false;
    const [yaw, elevation] = {
        front: [0, 0],
        threeQuarter: [-30, 24],
        back: [180, 0],
        side: [90, 0],
        top: [0, 84],
      }[current] || [-30, 24],
      rad = Math.PI / 180;
    camera.position.set(
      distance * Math.sin(yaw * rad) * Math.cos(elevation * rad),
      distance * Math.sin(elevation * rad),
      distance * Math.cos(yaw * rad) * Math.cos(elevation * rad),
    );
    camera.lookAt(0, 0, 0);
    controls.update();
    renderOnce();
  }
  function renderOnce() {
    if (!disposed && visible && onscreen && !contextLost && !document.hidden) {
      renderer.render(scene, camera);
      drawCount++;
    }
  }
  function loop(now = performance.now()) {
    frame = 0;
    if (disposed || !visible || !onscreen || contextLost || document.hidden) return;
    const delta = previousFrame ? Math.min((now - previousFrame) / 1000, 0.1) : 1 / 60;
    previousFrame = now;
    if (controls.update(delta)) renderOnce();
    frame = requestAnimationFrame(loop);
  }
  function visibility() {
    cancelAnimationFrame(frame);
    frame = 0;
    previousFrame = 0;
    if (visible && onscreen && !contextLost && !document.hidden) {
      renderOnce();
      loop();
    }
  }
  function resize() {
    if (disposed) return;
    const width = Math.max(container.clientWidth, 1),
      height = Math.max(container.clientHeight, 1);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderOnce();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(container);
  const intersection = new IntersectionObserver(([entry]) => {
    onscreen = entry.isIntersecting;
    visibility();
  });
  intersection.observe(container);
  const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  const motionChanged = () => {
    if (reducedMotion.matches) controls.autoRotate = false;
  };
  reducedMotion.addEventListener("change", motionChanged);
  const contextMessage = document.createElement("div");
  contextMessage.className = "context-fallback";
  contextMessage.hidden = true;
  contextMessage.innerHTML = '<p>3D preview stopped on this device. You can still see the front and back shapes.</p>' + fallback;
  container.append(contextMessage);
  const lost = (e) => {
    e.preventDefault();
    contextLost = true;
    renderer.domElement.hidden = true;
    contextMessage.hidden = false;
    visibility();
  };
  const restored = () => {
    contextLost = false;
    renderer.domElement.hidden = false;
    contextMessage.hidden = true;
    resize();
    visibility();
  };
  renderer.domElement.addEventListener("webglcontextlost", lost);
  renderer.domElement.addEventListener("webglcontextrestored", restored);
  document.addEventListener("visibilitychange", visibility);
  const onStart = () => {
    controls.autoRotate = false;
    current = "custom";
  };
  controls.addEventListener("start", onStart);
  // OrbitControls sets touch-action:none. Vertical gestures belong to the page.
  renderer.domElement.style.touchAction = "pan-y";
  let gesture = null;
  const pointers = new Map();
  let pinch = 0;
  const down = (e) => {
    if (e.pointerType !== "touch") return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    controls.enabled = false;
    gesture = {
      x: e.clientX,
      y: e.clientY,
      yaw: controls.getAzimuthalAngle(),
      polar: controls.getPolarAngle(),
      radius: camera.position.length(),
      horizontal: false,
    };
    onStart();
    if (pointers.size === 2) {
      const p = [...pointers.values()];
      pinch = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
    }
  };
  const move = (e) => {
    if (e.pointerType !== "touch" || locked || !gesture) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.size === 2) {
      const p = [...pointers.values()],
        d = Math.hypot(p[0].x - p[1].x, p[0].y - p[1].y);
      if (pinch && d) {
        camera.position.multiplyScalar(pinch / d);
        camera.position.setLength(
          Math.max(
            controls.minDistance,
            Math.min(controls.maxDistance, camera.position.length()),
          ),
        );
      }
      pinch = d;
      renderOnce();
      return;
    }
    const dx = e.clientX - gesture.x,
      dy = e.clientY - gesture.y;
    if (
      !gesture.horizontal &&
      Math.abs(dx) > 8 &&
      Math.abs(dx) > Math.abs(dy) * 1.2
    )
      gesture.horizontal = true;
    if (gesture.horizontal) {
      const s = new THREE.Spherical(
        gesture.radius,
        gesture.polar,
        gesture.yaw - dx * 0.012,
      );
      camera.position.setFromSpherical(s);
      camera.lookAt(0, 0, 0);
      renderOnce();
    }
  };
  const up = (e) => {
    pointers.delete(e.pointerId);
    if (!pointers.size) {
      gesture = null;
      controls.enabled = !locked;
    }
  };
  renderer.domElement.addEventListener("pointerdown", down, true);
  renderer.domElement.addEventListener("pointermove", move, true);
  renderer.domElement.addEventListener("pointerup", up, true);
  renderer.domElement.addEventListener("pointercancel", up, true);
  const key = (e) => {
    if (
      ![
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "+",
        "=",
        "-",
        "Home",
      ].includes(e.key)
    )
      return;
    e.preventDefault();
    if (locked && e.key.startsWith("Arrow")) return;
    onStart();
    if (e.key === "Home") {
      preset("reset");
      return;
    }
    const s = new THREE.Spherical().setFromVector3(camera.position);
    s.theta +=
      e.key === "ArrowLeft" ? 0.15 : e.key === "ArrowRight" ? -0.15 : 0;
    s.phi = Math.max(
      0.09,
      Math.min(
        Math.PI * 0.78,
        s.phi + (e.key === "ArrowUp" ? -0.1 : e.key === "ArrowDown" ? 0.1 : 0),
      ),
    );
    s.radius = Math.max(
      controls.minDistance,
      Math.min(
        controls.maxDistance,
        s.radius * (e.key === "-" ? 1.1 : ["+", "="].includes(e.key) ? 0.9 : 1),
      ),
    );
    camera.position.setFromSpherical(s);
    controls.update();
    renderOnce();
  };
  container.addEventListener("keydown", key);
  preset("threeQuarter");
  controls.autoRotate = false;
  resize();
  loop();
  const api = {
    preset,
    restorePattern() {
      modelLoadVersion++;
      if (imageModel) {
        scene.remove(imageModel);
        disposeModel(imageModel);
        imageModel = null;
      }
      group.visible = true;
      shadow.position.y = -H / 2 - 0.6;
      dimensionsShown = false;
      dimensionGroup.visible = false;
      label.hidden = true;
      seamLabel.hidden = !halves;
      renderOnce();
    },
    async loadModel(data) {
      const version = ++modelLoadVersion;
      const module = await import('./image-model.js');
      const result = await module.parseImageModel(data);
      if (disposed || version !== modelLoadVersion) {
        module.disposeImageModel(result.root);
        throw new DOMException('The design has changed.', 'AbortError');
      }
      if (imageModel) {
        scene.remove(imageModel);
        disposeModel(imageModel);
      }
      // Uniform display scaling preserves the generated shape. It does not
      // assert that inferred geometry matches physical sewing dimensions.
      const scale = Math.max(W, H) / Math.max(result.size.x, result.size.y, result.size.z);
      const center = result.bounds.getCenter(new THREE.Vector3());
      result.root.position.sub(center);
      imageModel = new THREE.Group();
      imageModel.add(result.root);
      imageModel.scale.setScalar(scale);
      scene.add(imageModel);
      disposeModel = module.disposeImageModel;
      group.visible = false;
      label.hidden = true;
      seamLabel.hidden = true;
      dimensionGroup.visible = false;
      dimensionsShown = false;
      shadow.position.y = -result.size.y * scale / 2 - 0.6;
      preset('threeQuarter');
      renderOnce();
    },
    dimensions(value) {
      if (imageModel) return;
      dimensionsShown = value;
      label.hidden = !value;
      dimensionGroup.visible = value;
      renderOnce();
    },
    seam(value) {
      if (imageModel) return;
      seamLabel.hidden = !value;
    },
    lock(value) {
      locked = value;
      controls.enabled = !value;
      controls.autoRotate = false;
      renderer.domElement.style.touchAction = value ? "auto" : "pan-y";
    },
    setVisible(value) {
      visible = value;
      visibility();
    },
    getState() {
      const a = new THREE.Spherical().setFromVector3(camera.position);
      group.updateMatrixWorld();
      const xs = [-W / 2, W / 2].flatMap((x) =>
        [-depth / 2, depth / 2].map(
          (z) => new THREE.Vector3(x, 0, z).project(camera).x,
        ),
      );
      return {
        source: imageModel ? 'image_to_3d' : 'pattern_preview',
        variant,
        frontHex: front.map((f) => f.hex),
        backHex: back.hex,
        yawDeg: (a.theta * 180) / Math.PI,
        zoomDistance: a.radius,
        preset: current,
        silhouetteWidthPx:
          ((Math.max(...xs) - Math.min(...xs)) * container.clientWidth) / 2,
        dimensionsShown,
        rendering: Boolean(frame),
        onscreen,
        contextLost,
        drawCount,
        locked,
      };
    },
    setYawForTest(degrees) {
      if (!test) return;
      onStart();
      const s = new THREE.Spherical().setFromVector3(camera.position);
      s.theta = (degrees * Math.PI) / 180;
      camera.position.setFromSpherical(s);
      controls.update();
      renderOnce();
    },
    dispose() {
      disposed = true;
      modelLoadVersion++;
      if (imageModel) disposeModel(imageModel);
      cancelAnimationFrame(frame);
      observer.disconnect();
      intersection.disconnect();
      reducedMotion.removeEventListener("change", motionChanged);
      renderer.domElement.removeEventListener("webglcontextlost", lost);
      renderer.domElement.removeEventListener("webglcontextrestored", restored);
      document.removeEventListener("visibilitychange", visibility);
      container.removeEventListener("keydown", key);
      controls.dispose();
      group.traverse((o) => {
        if (o.geometry) o.geometry.dispose();
      });
      dimensionGroup.children.forEach((o) => {
        o.geometry.dispose();
        o.material.dispose();
      });
      materials.forEach((m) => m.dispose());
      shadow.geometry.dispose();
      shadowMaterial.dispose();
      map.dispose();
      normalMap.dispose();
      shadowMap.dispose();
      renderer.dispose();
      container.innerHTML = "";
      if (test && window.__pouch3d === api) delete window.__pouch3d;
    },
  };
  if (test) window.__pouch3d = api;
  return api;
}
