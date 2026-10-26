import * as Cesium from "cesium";

import type { Map } from "./map";

// Light blue tint, so the placed building stands out from the existing buildings
const PLACED_BUILDING_COLOR = Cesium.Color.fromCssColorString("#4589ff");

// A BAG building (GLB from scripts/maak_gebouw_glb.py) placed on the map.
// The GLB is in local meters (x east, y north, z up) with the middle of the footprint at 0,0 and the ground at 0.
export class PlacedBuilding {
	public title: string;
	public longitude: number = 0;
	public latitude: number = 0;
	public heightAboveGround: number = 0;
	public heading: number = 0;
	public visible: boolean = true;

	// Called by the feature info when someone clicks on this building
	public onClick: (() => void) | undefined;

	private map: Map;
	private model: Cesium.Model | undefined;
	private groundHeight: number = 0;
	private footprint: Array<[number, number]>;

	constructor(map: Map, title: string, footprint: Array<[number, number]>) {
		this.map = map;
		this.title = title;
		this.footprint = footprint;
	}

	public async load(url: string): Promise<void> {
		// upAxis Z and forwardAxis X, so Cesium uses the GLB coordinates as they are.
		// id is what scene.pick() returns, so a click on the model leads back to this building.
		this.model = await Cesium.Model.fromGltfAsync({
			url: url,
			id: this,
			upAxis: Cesium.Axis.Z,
			forwardAxis: Cesium.Axis.X,
			shadows: Cesium.ShadowMode.ENABLED,
			color: PLACED_BUILDING_COLOR,
			colorBlendMode: Cesium.ColorBlendMode.MIX,
			colorBlendAmount: 0.4,
			show: false
		});
		this.map.viewer.scene.primitives.add(this.model);
	}

	public placeAt(groundPosition: Cesium.Cartesian3): void {
		const cartographic = Cesium.Cartographic.fromCartesian(groundPosition);
		this.longitude = Cesium.Math.toDegrees(cartographic.longitude);
		this.latitude = Cesium.Math.toDegrees(cartographic.latitude);
		this.groundHeight = cartographic.height;
		this.updateModel();
	}

	public moveTo(longitude: number, latitude: number): void {
		this.longitude = longitude;
		this.latitude = latitude;
		this.groundHeight = this.getGroundHeight() ?? this.groundHeight;
		this.updateModel();
	}

	public setHeightAboveGround(height: number): void {
		this.heightAboveGround = height;
		this.updateModel();
	}

	public setHeading(heading: number): void {
		this.heading = heading;
		this.updateModel();
	}

	public setVisible(visible: boolean): void {
		this.visible = visible;
		this.updateModel();
	}

	public isPickedModel(picked: any): boolean {
		return this.model !== undefined && picked?.primitive === this.model;
	}

	public getPosition(): Cesium.Cartesian3 {
		return Cesium.Cartesian3.fromDegrees(
			this.longitude,
			this.latitude,
			this.groundHeight + this.heightAboveGround
		);
	}

	// Outline of the building on the map, moved and turned with the model. Used to clip the buildings underneath.
	public getFootprint(): Array<Cesium.Cartesian3> {
		const matrix = this.getModelMatrix();
		return this.footprint.map(([x, y]) =>
			Cesium.Matrix4.multiplyByPoint(matrix, new Cesium.Cartesian3(x, y, 0), new Cesium.Cartesian3())
		);
	}

	public remove(): void {
		if (this.model) {
			this.map.viewer.scene.primitives.remove(this.model);
			this.model = undefined;
		}
		this.map.refresh();
	}

	private getModelMatrix(): Cesium.Matrix4 {
		const heading = new Cesium.HeadingPitchRoll(Cesium.Math.toRadians(this.heading), 0, 0);
		return Cesium.Transforms.headingPitchRollToFixedFrame(this.getPosition(), heading);
	}

	private updateModel(): void {
		if (!this.model) return;
		this.model.modelMatrix = this.getModelMatrix();
		this.model.show = this.visible;
		this.map.refresh();
	}

	private getGroundHeight(): number | undefined {
		const cartographic = Cesium.Cartographic.fromDegrees(this.longitude, this.latitude);
		return this.map.viewer.scene.globe.getHeight(cartographic);
	}
}
