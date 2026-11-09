import * as Cesium from "cesium";

import type { Map } from "./map";

// Light blue tint when there is no theme or analysis, so the placed building stands out
const PLACED_BUILDING_COLOR = Cesium.Color.fromCssColorString("#4589ff");

// A BAG building (GLB from scripts/maak_gebouw_glb.py) placed on the map.
// The GLB is in local meters (x east, y north, z up) with the middle of the footprint at 0,0 and the ground at 0.
export class PlacedBuilding {
	public title: string;
	// BAG attributes from the config, the same fields as in the tileset (bouwjaar, dakhoogte_max, ...)
	public attributes: Record<string, unknown>;
	public longitude: number = 0;
	public latitude: number = 0;
	public heightAboveGround: number = 0;
	public heading: number = 0;
	public visible: boolean = true;
	// Clip the existing buildings inside the footprint of this building
	public hideUnderneath: boolean = true;

	// Called by the feature info when someone clicks on this building
	public onClick: (() => void) | undefined;

	private map: Map;
	private model: Cesium.Model | undefined;
	private groundHeight: number = 0;
	private footprint: Array<[number, number]>;
	private tileset: Cesium.Cesium3DTileset | undefined;
	private tilesetStyle: Cesium.Cesium3DTileStyle | undefined;
	private stopFollowingTileset: (() => void) | undefined;

	constructor(
		map: Map,
		title: string,
		footprint: Array<[number, number]>,
		attributes: Record<string, unknown> = {}
	) {
		this.map = map;
		this.title = title;
		this.footprint = footprint;
		this.attributes = attributes;
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

	// Gives this building the same colours as the tileset: its theme and the distance analysis
	public followTileset(tileset: Cesium.Cesium3DTileset | undefined): void {
		this.tileset = tileset;
		this.stopFollowingTileset?.();
		this.stopFollowingTileset = this.map.viewer.scene.preRender.addEventListener(() => this.matchTileset());
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

	// Moves the camera to this building, looking at it from the south at an angle
	public flyTo(): void {
		const sphere = this.model?.ready
			? this.model.boundingSphere
			: new Cesium.BoundingSphere(this.getPosition(), 50);
		this.map.viewer.camera.flyToBoundingSphere(sphere, {
			duration: 1.5,
			offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-35), Math.max(sphere.radius * 4, 150))
		});
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
		this.stopFollowingTileset?.();
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

	// Runs before every frame, only changes the model when the tileset changed
	private matchTileset(): void {
		if (!this.model || !this.tileset) return;
		if (this.model.customShader === this.tileset.customShader && this.tilesetStyle === this.tileset.style) return;

		// undefined removes the shader again, Cesium's type just does not list it
		this.model.customShader = this.tileset.customShader as Cesium.CustomShader;
		this.tilesetStyle = this.tileset.style;

		// The empty theme makes every building white: then the blue tint, otherwise the same colour as the rest
		const themeColor = this.getThemeColor(this.tilesetStyle);
		const isWhite = themeColor.red === 1 && themeColor.green === 1 && themeColor.blue === 1;
		const showTint = isWhite && !this.model.customShader;

		this.model.color = showTint ? PLACED_BUILDING_COLOR : themeColor;
		this.model.colorBlendMode = showTint ? Cesium.ColorBlendMode.MIX : Cesium.ColorBlendMode.HIGHLIGHT;
	}

	// Runs the colour rules of the theme on the BAG attributes of this building
	private getThemeColor(style: Cesium.Cesium3DTileStyle | undefined): Cesium.Color {
		if (!style?.color) return Cesium.Color.WHITE;

		const feature = { getPropertyInherited: (name: string) => this.attributes[name] };
		try {
			return style.color.evaluateColor(feature as any, new Cesium.Color());
		} catch {
			// A rule that needs an attribute this building does not have
			return Cesium.Color.WHITE;
		}
	}

	private getGroundHeight(): number | undefined {
		const cartographic = Cesium.Cartographic.fromDegrees(this.longitude, this.latitude);
		return this.map.viewer.scene.globe.getHeight(cartographic);
	}
}
