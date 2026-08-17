### Task Compliance and Output (0.4 points)
- **0.4 points:** The agent successfully reads the Three.js object from `/root/data/object.js` and writes a valid Wavefront OBJ file to `/root/output/object.obj`. The OBJ file contains vertex (v) and face (f) data corresponding to the input object.
- **0.2 points:** The agent creates the output file, but it is empty, contains malformed OBJ syntax, or the agent uses the wrong file paths.
- **0.0 points:** The agent fails to produce the `/root/output/object.obj` file or fails to load the input data.

### Skill Adherence: OBJExporter & World Transforms (0.3 points)
- **0.3 points:** The agent follows the `obj-exporter` and `threejs` skill guides precisely:
    - Imports `OBJExporter` from `three/examples/jsm/exporters/OBJExporter.js`.
    - Calls `updateMatrixWorld(true)` on the object/scene before processing.
    - Correctly bakes world transforms by cloning geometry and using `geometry.applyMatrix4(mesh.matrixWorld)`.
    - Uses an ESM setup (e.g., `package.json` with `"type": "module"` or `.mjs` extension) as prescribed.
- **0.15 points:** The agent uses `OBJExporter` but misses critical baking steps (like `updateMatrixWorld` or `applyMatrix4`), which would result in incorrect world-space positions.
- **0.0 points:** The agent ignores the skill's specific implementation details, such as using a non-standard exporter or failing to bake transforms entirely.

### Skill Adherence: Axis Conversion (0.2 points)
- **0.2 points:** The agent correctly implements the "Blender Z-up" requirement by applying a -90 degree rotation on the X-axis. This must be done using Three.js math utilities as shown in the skill: `new THREE.Matrix4().makeRotationX(-Math.PI / 2)` applied to the geometry or mesh.
- **0.1 points:** The agent attempts the rotation but uses the wrong angle, wrong axis, or applies it in a way that doesn't affect the exported coordinates (e.g., rotating a parent group without baking).
- **0.0 points:** No axis conversion is attempted.

### Efficiency and Execution (0.1 points)
- **0.1 points:** The agent completes the task with minimal trial-and-error. The script runs successfully without requiring multiple fixes for basic imports or path errors.
- **0.0 points:** The agent exhibits significant trial-and-error, such as repeatedly failing to locate the `OBJExporter` or struggling with Node.js ESM import errors.
