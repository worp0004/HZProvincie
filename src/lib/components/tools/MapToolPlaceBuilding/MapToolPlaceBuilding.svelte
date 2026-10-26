<script lang="ts">
	import { getContext, onDestroy } from "svelte";
	import { get } from "svelte/store";
	import { _ } from "svelte-i18n";
	import {
		Button,
		Checkbox,
		InlineNotification,
		NumberInput,
		Select,
		SelectItem,
		Slider
	} from "carbon-components-svelte";
	import { Building } from "carbon-icons-svelte";
	import * as Cesium from "cesium";

	import type { MouseLocation } from "$lib/map-core/mouse-location";
	import { PlacedBuilding } from "$lib/map-cesium/placed-building";
	import { MapToolMenuOption } from "../MapToolMenuOption";

	export let id: string;
	export let label: string;
	export let icon: any = Building;

	const {
		registerTool,
		selectedTool,
		map,
		disableInteractionFromOtherTools,
		enableInteractionsFromOtherTools
	} = getContext<any>("mapTools");

	const tool = new MapToolMenuOption(id, icon, label);
	registerTool(tool);

	// One entry per GLB, scripts/maak_gebouw_glb.py prints these
	interface BuildingSettings {
		title: string;
		url: string;
		footprint?: Array<[number, number]>;
	}

	let buildings: Array<BuildingSettings> = [];
	let layerId: string | undefined;
	let selectedBuildingId: string | number = 0;

	let building: PlacedBuilding | undefined;
	let placedUrl: string | undefined;
	let loading = false;
	let loadFailed = false;
	let waitingForClick = false;
	let dragging = false;
	let dragHandler: Cesium.ScreenSpaceEventHandler | undefined;

	// Values shown in the panel
	let longitude = 0;
	let latitude = 0;
	let height = 0;
	let heading = 0;
	let showBuilding = true;
	let hideBuildingsUnderneath = true;
	let showSurroundings = true;

	tool.settings.subscribe((settings: any) => {
		buildings = settings?.buildings ?? [];
		layerId = settings?.layerId;
	});

	const unsubscribeSelectedTool = selectedTool.subscribe((selected: MapToolMenuOption) => {
		if (selected === tool) {
			startDragging();
			showSurroundings = getSurroundingsVisible();
		} else {
			stopPlacing();
			stopDragging();
		}
	});

	onDestroy(() => {
		unsubscribeSelectedTool();
		stopPlacing();
		stopDragging();
	});

	function getTilesetLayer(): any {
		return layerId ? map.getLayerById(layerId) : undefined;
	}

	// Position on the ground (terrain), existing buildings are ignored
	function getGroundPosition(windowPosition: Cesium.Cartesian2): Cesium.Cartesian3 | undefined {
		const ray = map.viewer.camera.getPickRay(windowPosition);
		return ray ? map.viewer.scene.globe.pick(ray, map.viewer.scene) : undefined;
	}

	// Number inputs can give a locale string like "4,5"
	function readNumber(value: unknown): number | undefined {
		const number = Number(String(value).replace(",", "."));
		return Number.isFinite(number) ? number : undefined;
	}

	function startPlacing(): void {
		if (waitingForClick) return;
		waitingForClick = true;
		disableInteractionFromOtherTools(id);
		map.on("mouseLeftClick", placeBuildingAtClick);
	}

	function stopPlacing(): void {
		if (!waitingForClick) return;
		waitingForClick = false;
		enableInteractionsFromOtherTools();
		map.off("mouseLeftClick", placeBuildingAtClick);
	}

	async function placeBuildingAtClick(location: MouseLocation): Promise<void> {
		const position = getGroundPosition(new Cesium.Cartesian2(location.x, location.y));
		if (!position) return;
		stopPlacing();

		const chosen = buildings[Number(selectedBuildingId)];
		if (!building || placedUrl !== chosen.url) {
			await loadBuilding(chosen);
		}
		if (!building) return;

		building.placeAt(position);
		afterBuildingChanged();
	}

	async function loadBuilding(chosen: BuildingSettings): Promise<void> {
		removeBuilding();
		loading = true;
		loadFailed = false;

		const newBuilding = new PlacedBuilding(map, chosen.title, chosen.footprint ?? []);
		newBuilding.visible = showBuilding;
		newBuilding.onClick = shareCenterWithAnalysis;
		try {
			await newBuilding.load(chosen.url);
			building = newBuilding;
			placedUrl = chosen.url;
		} catch (error) {
			console.error(error);
			loadFailed = true;
		}
		loading = false;
	}

	function removeBuilding(): void {
		if (!building) return;
		building.remove();
		building = undefined;
		placedUrl = undefined;
		updateClipArea();
	}

	function afterBuildingChanged(): void {
		updatePanel();
		updateClipArea();
		shareCenterWithAnalysis();
	}

	function updatePanel(): void {
		if (!building) return;
		longitude = Number(building.longitude.toFixed(6));
		latitude = Number(building.latitude.toFixed(6));
		height = building.heightAboveGround;
		heading = building.heading;
	}

	function updateClipArea(): void {
		const footprint = building && hideBuildingsUnderneath ? building.getFootprint() : [];
		getTilesetLayer()?.setClipArea(footprint.length >= 3 ? footprint : undefined);
	}

	// The distance analysis (tool "analysis") uses the placed building as its center
	function shareCenterWithAnalysis(): void {
		const layer = getTilesetLayer();
		if (building && layer?.selectedCenter) {
			layer.selectedCenter.set(building.getPosition());
		}
	}

	function onPositionChange(): void {
		const newLongitude = readNumber(longitude);
		const newLatitude = readNumber(latitude);
		if (!building || newLongitude === undefined || newLatitude === undefined) return;
		building.moveTo(newLongitude, newLatitude);
		afterBuildingChanged();
	}

	function onHeightChange(): void {
		const newHeight = readNumber(height);
		if (!building || newHeight === undefined) return;
		building.setHeightAboveGround(newHeight);
		afterBuildingChanged();
	}

	function onHeadingChange(): void {
		const newHeading = readNumber(heading);
		if (!building || newHeading === undefined) return;
		building.setHeading(newHeading);
		afterBuildingChanged();
	}

	function setBuildingVisible(visible: boolean): void {
		showBuilding = visible;
		building?.setVisible(visible);
	}

	function setHideBuildingsUnderneath(hide: boolean): void {
		hideBuildingsUnderneath = hide;
		updateClipArea();
	}

	function setSurroundingsVisible(visible: boolean): void {
		showSurroundings = visible;
		getTilesetLayer()?.visible.set(visible);
	}

	function getSurroundingsVisible(): boolean {
		const layer = getTilesetLayer();
		return layer ? get(layer.visible) : true;
	}

	// Drag the placed building over the map while this tool is open
	function startDragging(): void {
		if (dragHandler) return;
		dragHandler = new Cesium.ScreenSpaceEventHandler(map.viewer.canvas);
		dragHandler.setInputAction(onDragStart, Cesium.ScreenSpaceEventType.LEFT_DOWN);
		dragHandler.setInputAction(onDragMove, Cesium.ScreenSpaceEventType.MOUSE_MOVE);
		dragHandler.setInputAction(onDragEnd, Cesium.ScreenSpaceEventType.LEFT_UP);
	}

	function stopDragging(): void {
		dragHandler?.destroy();
		dragHandler = undefined;
		if (dragging) onDragEnd();
	}

	function onDragStart(event: { position: Cesium.Cartesian2 }): void {
		if (waitingForClick || !building) return;
		if (!building.isPickedModel(map.viewer.scene.pick(event.position))) return;
		dragging = true;
		lockCamera(true);
	}

	function onDragMove(event: { endPosition: Cesium.Cartesian2 }): void {
		if (!dragging || !building) return;
		const position = getGroundPosition(event.endPosition);
		if (!position) return;
		building.placeAt(position);
		updatePanel();
		updateClipArea();
	}

	function onDragEnd(): void {
		if (!dragging) return;
		dragging = false;
		lockCamera(false);
		shareCenterWithAnalysis();
	}

	function lockCamera(locked: boolean): void {
		map.viewer.scene.screenSpaceCameraController.enableInputs = !locked;
	}
</script>

{#if $selectedTool === tool}
	<div class="wrapper">
		<div class="section">
			{#if buildings.length === 0}
				<InlineNotification
					lowContrast
					hideCloseButton
					kind="info"
					title={$_("tools.placeBuilding.noBuildings")}
				/>
			{:else}
				<div class="field">
					<Select labelText={$_("tools.placeBuilding.building")} bind:selected={selectedBuildingId}>
						{#each buildings as option, index}
							<SelectItem value={index} text={option.title} />
						{/each}
					</Select>
				</div>
				<Button size="small" disabled={loading || waitingForClick} on:click={startPlacing}>
					{$_("tools.placeBuilding.place")}
				</Button>
				{#if waitingForClick}
					<p class="label-01 hint">{$_("tools.placeBuilding.clickMap")}</p>
				{/if}
				{#if loadFailed}
					<InlineNotification
						lowContrast
						hideCloseButton
						kind="error"
						title={$_("tools.placeBuilding.loadFailed")}
					/>
				{/if}
			{/if}
		</div>

		{#if building}
			<div class="divider" />
			<div class="section">
				<div class="label-02">{$_("tools.placeBuilding.visibility")}</div>
				<Checkbox
					labelText={$_("tools.placeBuilding.showBuilding")}
					checked={showBuilding}
					on:check={(e) => setBuildingVisible(e.detail)}
				/>
				<Checkbox
					labelText={$_("tools.placeBuilding.hideUnderneath")}
					checked={hideBuildingsUnderneath}
					on:check={(e) => setHideBuildingsUnderneath(e.detail)}
				/>
				<Checkbox
					labelText={$_("tools.placeBuilding.showSurroundings")}
					checked={showSurroundings}
					on:check={(e) => setSurroundingsVisible(e.detail)}
				/>
			</div>

			<div class="divider" />
			<div class="section">
				<div class="label-02">{$_("tools.placeBuilding.position")}</div>
				<p class="label-01 hint">{$_("tools.placeBuilding.dragHint")}</p>
				<div class="field">
					<NumberInput
						label={$_("tools.placeBuilding.longitude")}
						step={0.00001}
						bind:value={longitude}
						on:change={onPositionChange}
					/>
				</div>
				<div class="field">
					<NumberInput
						label={$_("tools.placeBuilding.latitude")}
						step={0.00001}
						bind:value={latitude}
						on:change={onPositionChange}
					/>
				</div>
				<div class="field">
					<NumberInput
						label={$_("tools.placeBuilding.height")}
						step={0.5}
						bind:value={height}
						on:change={onHeightChange}
					/>
				</div>
				<div class="field slider">
					<Slider
						labelText={$_("tools.placeBuilding.heading")}
						min={0}
						max={359}
						step={1}
						bind:value={heading}
						on:input={onHeadingChange}
						on:change={onHeadingChange}
					/>
				</div>
				<Button size="small" kind="danger-tertiary" on:click={removeBuilding}>
					{$_("tools.placeBuilding.remove")}
				</Button>
			</div>
		{/if}
	</div>
{/if}

<style>
	.wrapper {
		width: 100%;
	}

	.section {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		row-gap: var(--cds-spacing-03);
		padding: var(--cds-spacing-05);
	}

	.field {
		width: 100%;
	}

	.hint {
		margin: 0;
		color: var(--cds-text-secondary);
	}

	/* Carbon's slider has a default min-width that overflows a narrow panel */
	.slider :global(.bx--slider) {
		min-width: 0;
		flex: 1 1 auto;
	}

	.slider :global(.bx--slider-container) {
		width: 100%;
	}

	.divider {
		background-color: var(--cds-ui-03);
		width: 100%;
		height: 1px;
	}
</style>
