<script lang="ts">
	import { getContext, onDestroy, tick } from "svelte";
	import { get } from "svelte/store";
	import { _ } from "svelte-i18n";
	import {
		Button,
		Checkbox,
		FileUploaderButton,
		InlineNotification,
		NumberInput,
		Select,
		SelectItem,
		Slider,
		Tab,
		Tabs
	} from "carbon-components-svelte";
	import { Building, TrashCan } from "carbon-icons-svelte";
	import * as Cesium from "cesium";

	import type { MouseLocation } from "$lib/map-core/mouse-location";
	import { PlacedBuilding } from "$lib/map-cesium/placed-building";
	import { cityJsonToBuilding } from "$lib/map-cesium/cityjson-building";
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

	// Tab 0 is "Plaatsen" (choose and upload), tab 1 is "Geplaatst" (list and edit)
	const PLACE_TAB = 0;
	const PLACED_TAB = 1;

	// From the config (scripts/maak_gebouw_glb.py prints these) or uploaded by the user
	interface BuildingSettings {
		title: string;
		url: string;
		footprint?: Array<[number, number]>;
		attributes?: Record<string, unknown>;
		isGroup?: boolean;
	}

	let buildings: Array<BuildingSettings> = [];
	let layerId: string | undefined;
	let selectedBuildingId: string | number = 0;
	let selectedTab = PLACE_TAB;

	// All buildings on the map, and the one that is edited in the panel
	let placedBuildings: Array<PlacedBuilding> = [];
	let activeBuilding: PlacedBuilding | undefined;
	let placedList: HTMLElement | undefined;

	let loading = false;
	let loadFailed = false;
	let uploadFailed = false;
	let waitingForClick = false;
	let dragging = false;
	let dragHandler: Cesium.ScreenSpaceEventHandler | undefined;

	// Values of the active building shown in the panel
	let longitude = 0;
	let latitude = 0;
	let height = 0;
	let heading = 0;
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

	// Every click on "Plaats op de kaart" adds a new building, the ones already placed stay
	async function placeBuildingAtClick(location: MouseLocation): Promise<void> {
		const position = getGroundPosition(new Cesium.Cartesian2(location.x, location.y));
		if (!position) return;
		stopPlacing();

		const newBuilding = await loadBuilding(buildings[Number(selectedBuildingId)]);
		if (!newBuilding) return;

		newBuilding.placeAt(position);
		placedBuildings = [...placedBuildings, newBuilding];
		selectBuilding(newBuilding);
		updateClipAreas();
	}

	async function loadBuilding(chosen: BuildingSettings): Promise<PlacedBuilding | undefined> {
		loading = true;
		loadFailed = false;

		const newBuilding = new PlacedBuilding(map, chosen.title, chosen.footprint ?? [], chosen.attributes);
		newBuilding.onClick = () => selectBuilding(newBuilding);
		// The outline of a group also covers everything between its buildings, so the surroundings stay by default
		newBuilding.hideUnderneath = !chosen.isGroup;
		try {
			await newBuilding.load(chosen.url);
			newBuilding.followTileset(getTilesetLayer()?.source);
			return newBuilding;
		} catch (error) {
			console.error(error);
			loadFailed = true;
			return undefined;
		} finally {
			loading = false;
		}
	}

	// A 3DBAG CityJSON file from the user: add it to the choices and start placing it
	async function uploadBuilding(files: ReadonlyArray<File>): Promise<void> {
		const file = files[0];
		if (!file) return;

		uploadFailed = false;
		try {
			const uploaded = cityJsonToBuilding(await file.text());
			// A temporary url to the model in memory, the same way LEIA loads a dropped GLB file
			const url = URL.createObjectURL(new Blob([uploaded.glb], { type: "model/gltf-binary" }));
			buildings = [
				...buildings,
				{
					title: uploaded.title,
					url: url,
					footprint: uploaded.footprint,
					attributes: uploaded.attributes,
					isGroup: uploaded.isGroup
				}
			];
			selectedBuildingId = buildings.length - 1;
			startPlacing();
		} catch (error) {
			console.error(error);
			uploadFailed = true;
		}
	}

	// Makes a building the one that is edited and the center of the distance analysis, and shows it in the "Geplaatst" tab
	function selectBuilding(placed: PlacedBuilding): void {
		activeBuilding = placed;
		selectedTab = PLACED_TAB;
		updatePanel();
		shareCenterWithAnalysis();
		scrollActiveRowIntoView();
	}

	// With many buildings the list scrolls, keep the active one in view
	async function scrollActiveRowIntoView(): Promise<void> {
		await tick();
		placedList?.querySelector(".active")?.scrollIntoView({ block: "nearest" });
	}

	// Clicked in the list: select it and move the camera to it
	function selectAndFlyTo(placed: PlacedBuilding): void {
		selectBuilding(placed);
		placed.flyTo();
	}

	function removeBuilding(placed: PlacedBuilding): void {
		placed.remove();
		placedBuildings = placedBuildings.filter((other) => other !== placed);
		if (activeBuilding === placed) activeBuilding = undefined;
		updateClipAreas();
	}

	function afterBuildingChanged(): void {
		updatePanel();
		updateClipAreas();
		shareCenterWithAnalysis();
	}

	function updatePanel(): void {
		if (!activeBuilding) return;
		longitude = Number(activeBuilding.longitude.toFixed(6));
		latitude = Number(activeBuilding.latitude.toFixed(6));
		height = activeBuilding.heightAboveGround;
		heading = activeBuilding.heading;
	}

	// Clip the existing buildings under every placed building that has "bebouwing eronder weghalen" on
	function updateClipAreas(): void {
		const areas = placedBuildings.filter((placed) => placed.hideUnderneath).map((placed) => placed.getFootprint());
		getTilesetLayer()?.setClipAreas(areas);
	}

	// The distance analysis (tool "analysis") uses the active building as its center
	function shareCenterWithAnalysis(): void {
		const layer = getTilesetLayer();
		if (activeBuilding && layer?.selectedCenter) {
			layer.selectedCenter.set(activeBuilding.getPosition());
		}
	}

	function onPositionChange(): void {
		const newLongitude = readNumber(longitude);
		const newLatitude = readNumber(latitude);
		if (!activeBuilding || newLongitude === undefined || newLatitude === undefined) return;
		activeBuilding.moveTo(newLongitude, newLatitude);
		afterBuildingChanged();
	}

	function onHeightChange(): void {
		const newHeight = readNumber(height);
		if (!activeBuilding || newHeight === undefined) return;
		activeBuilding.setHeightAboveGround(newHeight);
		afterBuildingChanged();
	}

	function onHeadingChange(): void {
		const newHeading = readNumber(heading);
		if (!activeBuilding || newHeading === undefined) return;
		activeBuilding.setHeading(newHeading);
		afterBuildingChanged();
	}

	function setBuildingVisible(visible: boolean): void {
		activeBuilding?.setVisible(visible);
		placedBuildings = placedBuildings;
	}

	function setHideBuildingsUnderneath(hide: boolean): void {
		if (!activeBuilding) return;
		activeBuilding.hideUnderneath = hide;
		updateClipAreas();
	}

	function setSurroundingsVisible(visible: boolean): void {
		showSurroundings = visible;
		getTilesetLayer()?.visible.set(visible);
	}

	function getSurroundingsVisible(): boolean {
		const layer = getTilesetLayer();
		return layer ? get(layer.visible) : true;
	}

	// Drag any placed building over the map while this tool is open
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
		if (waitingForClick) return;
		const picked = map.viewer.scene.pick(event.position)?.id;
		if (!(picked instanceof PlacedBuilding) || !placedBuildings.includes(picked)) return;

		selectBuilding(picked);
		dragging = true;
		lockCamera(true);
	}

	function onDragMove(event: { endPosition: Cesium.Cartesian2 }): void {
		if (!dragging || !activeBuilding) return;
		const position = getGroundPosition(event.endPosition);
		if (!position) return;
		activeBuilding.placeAt(position);
		updatePanel();
		updateClipAreas();
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
		<div class="tabs">
			<Tabs autoWidth bind:selected={selectedTab}>
				<Tab label={$_("tools.placeBuilding.tabPlace")} />
				<Tab label={`${$_("tools.placeBuilding.tabPlaced")} (${placedBuildings.length})`} />
			</Tabs>
		</div>

		{#if selectedTab === PLACE_TAB}
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

			<div class="divider" />
			<div class="section">
				<div class="label-02">{$_("tools.placeBuilding.uploadTitle")}</div>
				<p class="label-01 hint">{$_("tools.placeBuilding.uploadHint")}</p>
				<FileUploaderButton
					labelText={$_("tools.placeBuilding.upload")}
					accept={[".json", ".jsonl"]}
					kind="tertiary"
					size="small"
					disableLabelChanges
					on:change={(e) => uploadBuilding(e.detail)}
				/>
				{#if uploadFailed}
					<InlineNotification
						lowContrast
						hideCloseButton
						kind="error"
						title={$_("tools.placeBuilding.uploadFailed")}
					/>
				{/if}
			</div>
		{:else}
			<div class="section">
				{#if placedBuildings.length === 0}
					<p class="label-01 hint">{$_("tools.placeBuilding.noPlaced")}</p>
				{:else}
					<p class="label-01 hint">{$_("tools.placeBuilding.listHint")}</p>
					<ul class="placed-list" bind:this={placedList}>
						{#each placedBuildings as placed, index (placed)}
							<li class="placed-row" class:active={placed === activeBuilding}>
								<button
									class="placed-item"
									class:hidden-building={!placed.visible}
									on:click={() => selectAndFlyTo(placed)}
								>
									<span class="placed-number">{index + 1}</span>
									<span class="placed-title" title={placed.title}>{placed.title}</span>
								</button>
								<button
									class="delete-button"
									title={$_("tools.placeBuilding.remove")}
									aria-label={$_("tools.placeBuilding.remove")}
									on:click={() => removeBuilding(placed)}
								>
									<TrashCan size={16} />
								</button>
							</li>
						{/each}
					</ul>
				{/if}
				<Checkbox
					labelText={$_("tools.placeBuilding.showSurroundings")}
					checked={showSurroundings}
					on:check={(e) => setSurroundingsVisible(e.detail)}
				/>
			</div>

			{#if activeBuilding}
				<div class="divider" />
				<div class="section">
					<div class="label-02">
						{$_("tools.placeBuilding.edit")}: {placedBuildings.indexOf(activeBuilding) + 1}. {activeBuilding.title}
					</div>
					<Checkbox
						labelText={$_("tools.placeBuilding.showBuilding")}
						checked={activeBuilding.visible}
						on:check={(e) => setBuildingVisible(e.detail)}
					/>
					<Checkbox
						labelText={$_("tools.placeBuilding.hideUnderneath")}
						checked={activeBuilding.hideUnderneath}
						on:check={(e) => setHideBuildingsUnderneath(e.detail)}
					/>

					<div class="label-02 subtitle">{$_("tools.placeBuilding.position")}</div>
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
				</div>
			{/if}
		{/if}
	</div>
{/if}

<style>
	.wrapper {
		width: 100%;
	}

	.tabs {
		padding: var(--cds-spacing-03) var(--cds-spacing-05) 0;
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

	.subtitle {
		padding-top: var(--cds-spacing-03);
	}

	/* Own scrollbar, so the edit part below stays in view when there are many buildings */
	.placed-list {
		width: 100%;
		max-height: 16rem;
		overflow-y: auto;
		list-style: none;
		margin: 0;
		padding: 0;
		border-bottom: 1px solid var(--cds-ui-03);
	}

	.placed-row {
		display: flex;
		align-items: center;
		border-top: 1px solid var(--cds-ui-03);
	}

	.placed-row:hover {
		background: var(--cds-hover-ui);
	}

	.placed-row.active {
		background: var(--cds-selected-ui);
	}

	.placed-item {
		flex: 1 1 auto;
		min-width: 0;
		min-height: 2.5rem;
		display: flex;
		align-items: center;
		column-gap: var(--cds-spacing-03);
		padding: 0 var(--cds-spacing-03);
		border: none;
		background: transparent;
		color: var(--cds-text-01);
		font: inherit;
		text-align: left;
		cursor: pointer;
	}

	.placed-row.active .placed-item {
		font-weight: 600;
	}

	.placed-item.hidden-building {
		color: var(--cds-text-03);
	}

	.placed-number {
		flex: 0 0 1.5rem;
		color: var(--cds-text-02);
	}

	.placed-title {
		flex: 1 1 auto;
		min-width: 0;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		font-size: 0.875rem;
	}

	/* Neutral at rest and red on hover, a column of red bins would look like an error */
	.delete-button {
		flex: 0 0 2.5rem;
		height: 2.5rem;
		display: flex;
		align-items: center;
		justify-content: center;
		border: none;
		background: transparent;
		color: var(--cds-icon-primary);
		cursor: pointer;
	}

	.delete-button:hover,
	.delete-button:focus-visible {
		color: var(--cds-support-01);
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
