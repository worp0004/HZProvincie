import {
	Cartesian3,
	ConstantPositionProperty,
	ConstantProperty,
	Entity,
	HeadingPitchRoll,
	HeightReference,
	JulianDate,
	Math as CesiumMath,
	ShadowMode,
	Transforms,
	type Viewer
} from "cesium";

const OBJECT_PREFIX = "placed-object-";


export interface Add3DObjectOptions {
	id: string;
	name?: string;

	modelUrl: string;

	longitude: number;
	latitude: number;
	height?: number;

	heading?: number;
	pitch?: number;
	roll?: number;

	scale?: number;

	visible?: boolean;
	shadows?: boolean;
}


export interface Move3DObjectOptions {
	longitude: number;
	latitude: number;
	height?: number;

	heading?: number;
	pitch?: number;
	roll?: number;
}


/*
 * LEIA gebruikt requestRenderMode.
 * Daarom vragen we na wijzigingen
 * handmatig een nieuwe render aan.
 */
function refreshViewer(viewer: Viewer): void {
	viewer.scene.requestRender();
}


/*
 * Geeft onze geplaatste objecten
 * een herkenbare prefix.
 */
function getEntityId(id: string): string {
	if (id.startsWith(OBJECT_PREFIX)) {
		return id;
	}

	return `${OBJECT_PREFIX}${id}`;
}


/*
 * Maakt de rotatie van een model.
 */
function createOrientation(
	position: Cartesian3,
	heading: number = 0,
	pitch: number = 0,
	roll: number = 0
) {
	const hpr = new HeadingPitchRoll(
		CesiumMath.toRadians(heading),
		CesiumMath.toRadians(pitch),
		CesiumMath.toRadians(roll)
	);

	return Transforms.headingPitchRollQuaternion(
		position,
		hpr
	);
}


/*
 * 3D-object toevoegen met longitude/latitude.
 */
export function add3DObject(
	viewer: Viewer,
	options: Add3DObjectOptions
): Entity {

	const id = getEntityId(options.id);

	viewer.entities.removeById(id);

	const height = options.height ?? 0;
	const heading = options.heading ?? 0;
	const pitch = options.pitch ?? 0;
	const roll = options.roll ?? 0;
	const scale = options.scale ?? 1;
	const visible = options.visible ?? true;
	const shadows = options.shadows ?? true;

	const position = Cartesian3.fromDegrees(
		options.longitude,
		options.latitude,
		height
	);

	const orientation = createOrientation(
		position,
		heading,
		pitch,
		roll
	);

	const entity = viewer.entities.add({
		id: id,

		name:
			options.name ??
			options.id,

		position: position,

		orientation: orientation,

		show: visible,

		model: {
			uri: options.modelUrl,

			scale: scale,

			heightReference:
				HeightReference.RELATIVE_TO_GROUND,

			shadows:
				shadows
					? ShadowMode.ENABLED
					: ShadowMode.DISABLED
		}
	});

	refreshViewer(viewer);

	return entity;
}


/*
 * 3D-object toevoegen met een
 * bestaande Cesium Cartesian3.
 *
 * Deze is later handig voor drag-and-drop.
 */
export function add3DObjectAtPosition(
	viewer: Viewer,
	options: {
		id: string;
		name?: string;

		modelUrl: string;

		position: Cartesian3;

		heading?: number;
		pitch?: number;
		roll?: number;

		scale?: number;

		shadows?: boolean;
	}
): Entity {

	const id = getEntityId(options.id);

	viewer.entities.removeById(id);

	const heading = options.heading ?? 0;
	const pitch = options.pitch ?? 0;
	const roll = options.roll ?? 0;
	const scale = options.scale ?? 1;
	const shadows = options.shadows ?? true;

	const orientation = createOrientation(
		options.position,
		heading,
		pitch,
		roll
	);

	const entity = viewer.entities.add({
		id: id,

		name:
			options.name ??
			options.id,

		position:
			options.position,

		orientation:
			orientation,

		model: {
			uri:
				options.modelUrl,

			scale:
				scale,

			shadows:
				shadows
					? ShadowMode.ENABLED
					: ShadowMode.DISABLED
		}
	});

	refreshViewer(viewer);

	return entity;
}


/*
 * Object zoeken.
 */
export function get3DObject(
	viewer: Viewer,
	id: string
): Entity | undefined {

	return viewer.entities.getById(
		getEntityId(id)
	);
}


/*
 * Object verplaatsen.
 */
export function move3DObject(
	viewer: Viewer,
	id: string,
	options: Move3DObjectOptions
): void {

	const entity = get3DObject(
		viewer,
		id
	);

	if (!entity) {
		console.error(
			`3D-object "${id}" bestaat niet.`
		);

		return;
	}

	const position = Cartesian3.fromDegrees(
		options.longitude,
		options.latitude,
		options.height ?? 0
	);

	const orientation = createOrientation(
		position,
		options.heading ?? 0,
		options.pitch ?? 0,
		options.roll ?? 0
	);

	entity.position =
		new ConstantPositionProperty(position);

	entity.orientation =
		new ConstantProperty(orientation);

	refreshViewer(viewer);
}


/*
 * Object draaien.
 */
export function rotate3DObject(
	viewer: Viewer,
	id: string,
	heading: number,
	pitch: number = 0,
	roll: number = 0
): void {

	const entity = get3DObject(
		viewer,
		id
	);

	if (!entity) {
		console.error(
			`3D-object "${id}" bestaat niet.`
		);

		return;
	}

	if (!entity.position) {
		console.error(
			`3D-object "${id}" heeft geen positie.`
		);

		return;
	}

	const position = entity.position.getValue(
		viewer.clock.currentTime
	);

	if (!position) {
		console.error(
			`Positie van "${id}" niet gevonden.`
		);

		return;
	}

	entity.orientation =
		new ConstantProperty(
			createOrientation(
				position,
				heading,
				pitch,
				roll
			)
		);

	refreshViewer(viewer);
}


/*
 * Grootte aanpassen.
 */
export function set3DObjectScale(
	viewer: Viewer,
	id: string,
	scale: number
): void {

	const entity = get3DObject(
		viewer,
		id
	);

	if (!entity?.model) {
		console.error(
			`3D-model "${id}" niet gevonden.`
		);

		return;
	}

	entity.model.scale =
		new ConstantProperty(scale);

	refreshViewer(viewer);
}


/*
 * Zichtbaarheid aanpassen.
 */
export function set3DObjectVisible(
	viewer: Viewer,
	id: string,
	visible: boolean
): void {

	const entity = get3DObject(
		viewer,
		id
	);

	if (!entity) {
		console.error(
			`3D-object "${id}" niet gevonden.`
		);

		return;
	}

	entity.show = visible;

	refreshViewer(viewer);
}


/*
 * Schaduw van één model aan/uit.
 */
export function set3DObjectShadows(
	viewer: Viewer,
	id: string,
	enabled: boolean
): void {

	const entity = get3DObject(
		viewer,
		id
	);

	if (!entity?.model) {
		console.error(
			`3D-model "${id}" niet gevonden.`
		);

		return;
	}

	entity.model.shadows =
		new ConstantProperty(
			enabled
				? ShadowMode.ENABLED
				: ShadowMode.DISABLED
		);

	refreshViewer(viewer);
}


/*
 * Eén object verwijderen.
 */
export function remove3DObject(
	viewer: Viewer,
	id: string
): boolean {

	const removed =
		viewer.entities.removeById(
			getEntityId(id)
		);

	refreshViewer(viewer);

	return removed;
}


/*
 * Alle door ons toegevoegde objecten verwijderen.
 */
export function removeAll3DObjects(
	viewer: Viewer
): void {

	const entities = [
		...viewer.entities.values
	];

	for (const entity of entities) {

		if (
			entity.id.startsWith(
				OBJECT_PREFIX
			)
		) {
			viewer.entities.remove(
				entity
			);
		}
	}

	refreshViewer(viewer);
}


/*
 * Globale shadows aan/uit.
 */
export function setViewerShadows(
	viewer: Viewer,
	enabled: boolean
): void {

	viewer.shadows = enabled;

	viewer.terrainShadows =
		enabled
			? ShadowMode.ENABLED
			: ShadowMode.DISABLED;

	viewer.scene.globe.enableLighting =
		enabled;

	refreshViewer(viewer);
}


/*
 * Datum/tijd van de zon aanpassen.
 */
export function setSunTime(
	viewer: Viewer,
	isoDate: string
): void {

	viewer.clock.shouldAnimate = false;

	viewer.clock.currentTime =
		JulianDate.fromIso8601(
			isoDate
		);

	refreshViewer(viewer);
}