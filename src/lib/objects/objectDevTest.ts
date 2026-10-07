import {
	HeadingPitchRange,
	Math as CesiumMath,
	type Viewer
} from "cesium";

import {
	add3DObject,
	get3DObject,
	move3DObject,
	rotate3DObject,
	remove3DObject,
	removeAll3DObjects,
	set3DObjectScale,
	set3DObjectShadows,
	set3DObjectVisible,
	setViewerShadows,
	setSunTime
} from "./objectManager";


const TEST_OBJECT_ID =
	"test-building";


const state = {
	longitude: 3.61389,
	latitude: 51.49880,
	height: 10,
	scale: 20,
	heading: 0
};


export function setupObjectDevTest(
	viewer: Viewer
): void {

	const objectTest = {

		add(
			longitude = state.longitude,
			latitude = state.latitude,
			height = state.height,
			scale = state.scale,
			heading = state.heading
		) {

			state.longitude = longitude;
			state.latitude = latitude;
			state.height = height;
			state.scale = scale;
			state.heading = heading;

			const entity = add3DObject(
				viewer,
				{
					id: TEST_OBJECT_ID,

					name:
						"Testgebouw",

					modelUrl:
						"/models/molen_11230.glb",

					longitude:
						longitude,

					latitude:
						latitude,

					height:
						height,

					scale:
						scale,

					heading:
						heading,
                        
                    pitch: 90,
		            
                    roll: 0,    

					shadows:
						true
				}
			);

			console.log(
				"3D-object toegevoegd:",
				entity
			);

			return entity;
		},


		fly() {

			const entity = get3DObject(
				viewer,
				TEST_OBJECT_ID
			);

			if (!entity) {
				console.warn(
					"Voeg eerst het object toe met objectTest.add()"
				);

				return;
			}

			return viewer.flyTo(
				entity,
				{
					duration: 1.5,

					offset:
						new HeadingPitchRange(
							CesiumMath.toRadians(
								30
							),

							CesiumMath.toRadians(
								-25
							),

							150
						)
				}
			);
		},


		move(
			longitude: number,
			latitude: number,
			height = state.height
		) {

			state.longitude =
				longitude;

			state.latitude =
				latitude;

			state.height =
				height;

			move3DObject(
				viewer,
				TEST_OBJECT_ID,
				{
					longitude:
						longitude,

					latitude:
						latitude,

					height:
						height,

					heading:
						state.heading
				}
			);

			console.log(
				"Object verplaatst:",
				longitude,
				latitude,
				height
			);
		},


		rotate(
			heading: number
		) {

			state.heading =
				heading;

			rotate3DObject(
				viewer,
				TEST_OBJECT_ID,
				heading
			);

			console.log(
				"Object gedraaid:",
				heading
			);
		},


		scale(
			scale: number
		) {

			state.scale =
				scale;

			set3DObjectScale(
				viewer,
				TEST_OBJECT_ID,
				scale
			);

			console.log(
				"Schaal:",
				scale
			);
		},


		hide() {

			set3DObjectVisible(
				viewer,
				TEST_OBJECT_ID,
				false
			);

			console.log(
				"Object verborgen"
			);
		},


		show() {

			set3DObjectVisible(
				viewer,
				TEST_OBJECT_ID,
				true
			);

			console.log(
				"Object zichtbaar"
			);
		},


		remove() {

			const removed =
				remove3DObject(
					viewer,
					TEST_OBJECT_ID
				);

			console.log(
				"Object verwijderd:",
				removed
			);

			return removed;
		},


		removeAll() {

			removeAll3DObjects(
				viewer
			);

			console.log(
				"Alle geplaatste objecten verwijderd"
			);
		},


		shadows(
			enabled: boolean = true
		) {

			setViewerShadows(
				viewer,
				enabled
			);

			set3DObjectShadows(
				viewer,
				TEST_OBJECT_ID,
				enabled
			);

			console.log(
				"Shadows:",
				enabled
			);
		},


		sun(
			isoDate: string =
				"2026-06-21T12:00:00Z"
		) {

			setSunTime(
				viewer,
				isoDate
			);

			console.log(
				"Zon ingesteld:",
				isoDate
			);
		},


		state() {

			console.table(
				state
			);

			return {
				...state
			};
		}
	};


	(window as any).objectTest =
		objectTest;


	console.log(
		"Object testtool geladen"
	);

	console.log(
		"Commands:"
	);

	console.log(
		"objectTest.add()"
	);

	console.log(
		"objectTest.fly()"
	);

	console.log(
		"objectTest.move(3.614, 51.499)"
	);

	console.log(
		"objectTest.rotate(45)"
	);

	console.log(
		"objectTest.scale(30)"
	);

	console.log(
		"objectTest.hide()"
	);

	console.log(
		"objectTest.show()"
	);

	console.log(
		"objectTest.remove()"
	);

	console.log(
		"objectTest.shadows(true)"
	);

	console.log(
		"objectTest.sun('2026-06-21T12:00:00Z')"
	);
}