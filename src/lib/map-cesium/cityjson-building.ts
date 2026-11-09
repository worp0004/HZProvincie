import * as Cesium from "cesium";

// Reads a 3DBAG CityJSON file in the browser (one building or a group) and turns it into a model for the tool "Gebouw plaatsen".
// Same approach as scripts/maak_gebouw_glb.py: geometry from the BuildingParts, LoD 2.2 when it is there.

// Cesium's own earcut triangulation, it is part of Cesium but not listed in its types
const PolygonPipeline = (Cesium as any).PolygonPipeline as {
	triangulate(positions: Array<Cesium.Cartesian2>, holes?: Array<number>): Array<number>;
};

// 3DBAG attribute -> name in the Zeeland tileset, so the themes also work on an uploaded building
const TILESET_ATTRIBUTES: Record<string, string> = {
	bag_id: "identificatie",
	bouwjaar: "oorspronkelijkbouwjaar",
	status: "status",
	daktype: "b3_dak_type",
	aantal_bouwlagen: "b3_bouwlagen",
	maaiveldhoogte: "b3_h_maaiveld",
	dakhoogte_max: "b3_h_dak_max",
	nokhoogte: "b3_h_nok"
};

// 3DBAG stores whole millimeters, a loose CityJSONFeature does not carry that itself
const THREEDBAG_TRANSFORM = { scale: [0.001, 0.001, 0.001], translate: [0, 0, 0] };

export interface UploadedBuilding {
	title: string;
	glb: ArrayBuffer;
	footprint: Array<[number, number]>;
	attributes: Record<string, unknown>;
	isGroup: boolean;
}

type Point = [number, number, number];

export function cityJsonToBuilding(text: string): UploadedBuilding {
	const cityJson = readCityJson(text);
	const vertices = getRealVertices(cityJson);
	const buildingIds = Object.keys(cityJson.CityObjects).filter((id) => cityJson.CityObjects[id].type === "Building");
	if (buildingIds.length === 0) throw new Error("No buildings in this file");

	const triangles: Array<[number, number, number]> = [];
	for (const id of buildingIds) {
		for (const part of getBuildingParts(cityJson, id)) {
			const geometry = pickBestGeometry(part);
			if (!geometry) continue;
			for (const surface of getSurfaces(geometry)) {
				for (const triangle of triangulateSurface(surface, vertices)) triangles.push(triangle);
			}
		}
	}
	if (triangles.length === 0) throw new Error("No usable geometry in this file");

	const localPoints = toLocalMeters(vertices, triangles);
	return {
		title: makeTitle(cityJson, buildingIds),
		glb: createGlb(localPoints, triangles),
		footprint: getFootprint(localPoints, triangles),
		attributes: buildingIds.length === 1 ? getTilesetAttributes(cityJson.CityObjects[buildingIds[0]]) : {},
		isGroup: buildingIds.length > 1
	};
}

// A normal CityJSON file, a single CityJSONFeature, or CityJSONSeq (.jsonl: first line the header, then one feature per line)
function readCityJson(text: string): any {
	try {
		const json = JSON.parse(text);
		return json.type === "CityJSONFeature"
			? { transform: THREEDBAG_TRANSFORM, CityObjects: json.CityObjects, vertices: json.vertices }
			: json;
	} catch {
		return mergeCityJsonSeq(text);
	}
}

function mergeCityJsonSeq(text: string): any {
	const lines = text.split("\n").filter((line) => line.trim());
	const header = JSON.parse(lines[0]);
	const merged = { transform: header.transform, CityObjects: {} as Record<string, any>, vertices: [] as Array<Point> };
	for (const line of lines.slice(1)) {
		const feature = JSON.parse(line);
		const offset = merged.vertices.length;
		for (const [id, object] of Object.entries<any>(feature.CityObjects)) {
			for (const geometry of object.geometry ?? []) {
				geometry.boundaries = shiftIndices(geometry.boundaries, offset);
			}
			merged.CityObjects[id] = object;
		}
		merged.vertices.push(...feature.vertices);
	}
	return merged;
}

function shiftIndices(boundaries: any, offset: number): any {
	return Array.isArray(boundaries) ? boundaries.map((b) => shiftIndices(b, offset)) : boundaries + offset;
}

// CityJSON stores whole numbers, scale and translate turn them into RD meters and NAP height
function getRealVertices(cityJson: any): Array<Point> {
	const scale = cityJson.transform?.scale ?? [1, 1, 1];
	const translate = cityJson.transform?.translate ?? [0, 0, 0];
	return cityJson.vertices.map((v: Point) => [
		v[0] * scale[0] + translate[0],
		v[1] * scale[1] + translate[1],
		v[2] * scale[2] + translate[2]
	]);
}

// The real 3D shape is in the BuildingParts (children), the Building itself often only has a footprint
function getBuildingParts(cityJson: any, buildingId: string): Array<any> {
	const building = cityJson.CityObjects[buildingId];
	const parts = (building.children ?? [])
		.map((childId: string) => cityJson.CityObjects[childId])
		.filter((child: any) => child?.geometry?.length);
	return parts.length > 0 ? parts : [building];
}

function pickBestGeometry(cityObject: any): any {
	const geometries = cityObject.geometry ?? [];
	return (
		geometries.find((g: any) => String(g.lod) === "2.2") ??
		[...geometries].sort((a: any, b: any) => Number(b.lod) - Number(a.lod))[0]
	);
}

// A surface is a list of rings, the first ring is the outside and the rest are holes
function getSurfaces(geometry: any): Array<Array<Array<number>>> {
	const boundaries = geometry.boundaries;
	switch (geometry.type) {
		case "MultiSurface":
		case "CompositeSurface":
			return boundaries;
		case "Solid":
			return boundaries.flat(1);
		case "MultiSolid":
		case "CompositeSolid":
			return boundaries.flat(2);
		default:
			return [];
	}
}

// Same idea as v7: lay the surface flat in its own plane and let earcut make triangles
function triangulateSurface(surface: Array<Array<number>>, vertices: Array<Point>): Array<[number, number, number]> {
	const rings = surface.map(removeClosingPoint).filter((ring) => ring.length >= 3);
	if (rings.length === 0) return [];

	const normal = newellNormal(rings[0].map((i) => vertices[i]));
	const [u, v] = planeAxes(normal);
	const origin = vertices[rings[0][0]];

	const indices: Array<number> = [];
	const points2d: Array<Cesium.Cartesian2> = [];
	const holes: Array<number> = [];
	rings.forEach((ring, ringNumber) => {
		if (ringNumber > 0) holes.push(indices.length);
		for (const index of ring) {
			const relative = subtract(vertices[index], origin);
			indices.push(index);
			points2d.push(new Cesium.Cartesian2(dot(relative, u), dot(relative, v)));
		}
	});

	const result = PolygonPipeline.triangulate(points2d, holes);
	const triangles: Array<[number, number, number]> = [];
	for (let i = 0; i < result.length; i += 3) {
		triangles.push([indices[result[i]], indices[result[i + 1]], indices[result[i + 2]]]);
	}
	return triangles;
}

function removeClosingPoint(ring: Array<number>): Array<number> {
	return ring.length > 1 && ring[0] === ring[ring.length - 1] ? ring.slice(0, -1) : ring;
}

function newellNormal(points: Array<Point>): Point {
	const normal: Point = [0, 0, 0];
	points.forEach((p, i) => {
		const q = points[(i + 1) % points.length];
		normal[0] += (p[1] - q[1]) * (p[2] + q[2]);
		normal[1] += (p[2] - q[2]) * (p[0] + q[0]);
		normal[2] += (p[0] - q[0]) * (p[1] + q[1]);
	});
	return normalize(normal);
}

function planeAxes(normal: Point): [Point, Point] {
	const reference: Point = Math.abs(normal[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
	const u = normalize(cross(reference, normal));
	return [u, cross(normal, u)];
}

// RD is already in meters and close enough to east/north for a single building or a group of houses.
// Middle of the footprint at 0,0 and the lowest point at height 0.
function toLocalMeters(vertices: Array<Point>, triangles: Array<[number, number, number]>): Array<Point> {
	const [min, max] = getBounds(vertices, triangles);
	const middleX = (min[0] + max[0]) / 2;
	const middleY = (min[1] + max[1]) / 2;
	return vertices.map((p) => [p[0] - middleX, p[1] - middleY, p[2] - min[2]]);
}

// Lowest and highest x, y and z of the points the triangles use
function getBounds(points: Array<Point>, triangles: Array<[number, number, number]>): [Point, Point] {
	const min: Point = [Infinity, Infinity, Infinity];
	const max: Point = [-Infinity, -Infinity, -Infinity];
	for (const triangle of triangles) {
		for (const index of triangle) {
			for (let axis = 0; axis < 3; axis++) {
				min[axis] = Math.min(min[axis], points[index][axis]);
				max[axis] = Math.max(max[axis], points[index][axis]);
			}
		}
	}
	return [min, max];
}

// Outline seen from above (convex hull) with half a meter extra, the tool clips the existing buildings inside it
function getFootprint(points: Array<Point>, triangles: Array<[number, number, number]>): Array<[number, number]> {
	const used = [...new Set(triangles.flat())].map((i): [number, number] => [points[i][0], points[i][1]]);
	return convexHull(used).map(([x, y]) => {
		const length = Math.hypot(x, y) || 1;
		return [round(x + (x / length) * 0.5), round(y + (y / length) * 0.5)];
	});
}

function convexHull(points: Array<[number, number]>): Array<[number, number]> {
	const sorted = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
	const turn = (o: [number, number], a: [number, number], b: [number, number]) =>
		(a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
	const lower: Array<[number, number]> = [];
	for (const p of sorted) {
		while (lower.length >= 2 && turn(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
		lower.push(p);
	}
	const upper: Array<[number, number]> = [];
	for (const p of sorted.reverse()) {
		while (upper.length >= 2 && turn(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
		upper.push(p);
	}
	return [...lower.slice(0, -1), ...upper.slice(0, -1)];
}

function getTilesetAttributes(building: any): Record<string, unknown> {
	const attributes: Record<string, unknown> = {};
	for (const [tilesetName, bagName] of Object.entries(TILESET_ATTRIBUTES)) {
		attributes[tilesetName] = building.attributes?.[bagName] ?? "";
	}
	return attributes;
}

function makeTitle(cityJson: any, buildingIds: Array<string>): string {
	if (buildingIds.length > 1) return `Upload: ${buildingIds.length} panden`;
	const id = buildingIds[0].split(".").pop();
	const year = cityJson.CityObjects[buildingIds[0]].attributes?.oorspronkelijkbouwjaar;
	return year ? `Upload: pand ${id} (${year})` : `Upload: pand ${id}`;
}

// A small GLB: every triangle gets its own three points and a flat normal, one grey material on both sides
function createGlb(points: Array<Point>, triangles: Array<[number, number, number]>): ArrayBuffer {
	const positions = new Float32Array(triangles.length * 9);
	const normals = new Float32Array(triangles.length * 9);
	triangles.forEach((triangle, t) => {
		const [a, b, c] = triangle.map((i) => points[i]);
		const normal = normalize(cross(subtract(b, a), subtract(c, a)));
		[a, b, c].forEach((p, corner) => {
			positions.set(p, t * 9 + corner * 3);
			normals.set(normal, t * 9 + corner * 3);
		});
	});

	const [min, max] = getBounds(points, triangles);
	const count = positions.length / 3;
	const gltf = {
		asset: { version: "2.0" },
		scene: 0,
		scenes: [{ nodes: [0] }],
		nodes: [{ mesh: 0 }],
		meshes: [{ primitives: [{ attributes: { POSITION: 0, NORMAL: 1 }, material: 0 }] }],
		materials: [
			{
				pbrMetallicRoughness: { baseColorFactor: [0.8, 0.8, 0.8, 1], metallicFactor: 0, roughnessFactor: 1 },
				doubleSided: true
			}
		],
		buffers: [{ byteLength: positions.byteLength * 2 }],
		bufferViews: [
			{ buffer: 0, byteOffset: 0, byteLength: positions.byteLength, target: 34962 },
			{ buffer: 0, byteOffset: positions.byteLength, byteLength: normals.byteLength, target: 34962 }
		],
		accessors: [
			{ bufferView: 0, componentType: 5126, count: count, type: "VEC3", min: min, max: max },
			{ bufferView: 1, componentType: 5126, count: count, type: "VEC3" }
		]
	};

	// GLB = header, JSON chunk and binary chunk, both padded to a multiple of 4 bytes
	const json = new TextEncoder().encode(JSON.stringify(gltf));
	const jsonLength = Math.ceil(json.length / 4) * 4;
	const binaryLength = positions.byteLength * 2;
	const glb = new Uint8Array(12 + 8 + jsonLength + 8 + binaryLength);
	const view = new DataView(glb.buffer);
	view.setUint32(0, 0x46546c67, true); // "glTF"
	view.setUint32(4, 2, true);
	view.setUint32(8, glb.length, true);
	view.setUint32(12, jsonLength, true);
	view.setUint32(16, 0x4e4f534a, true); // "JSON"
	glb.fill(0x20, 20, 20 + jsonLength);
	glb.set(json, 20);
	view.setUint32(20 + jsonLength, binaryLength, true);
	view.setUint32(24 + jsonLength, 0x004e4942, true); // "BIN"
	glb.set(new Uint8Array(positions.buffer), 28 + jsonLength);
	glb.set(new Uint8Array(normals.buffer), 28 + jsonLength + positions.byteLength);
	return glb.buffer;
}

function subtract(a: Point, b: Point): Point {
	return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function dot(a: Point, b: Point): number {
	return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function cross(a: Point, b: Point): Point {
	return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function normalize(p: Point): Point {
	const length = Math.hypot(p[0], p[1], p[2]) || 1;
	return [p[0] / length, p[1] / length, p[2] / length];
}

function round(value: number): number {
	return Math.round(value * 10) / 10;
}
