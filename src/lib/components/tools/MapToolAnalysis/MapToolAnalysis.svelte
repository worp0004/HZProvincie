<script lang="ts">
	import { getContext, onDestroy } from "svelte";
	import { get, type Writable } from "svelte/store";
	import { _ } from "svelte-i18n";
	import { Button, InlineNotification, Slider } from "carbon-components-svelte";
	import { Analytics } from "carbon-icons-svelte";

	import { MapToolMenuOption } from "../MapToolMenuOption";

	export let id: string;
	export let label: string;
	export let icon: any = Analytics;

	const { registerTool, selectedTool, map } = getContext<any>("mapTools");

	const tool = new MapToolMenuOption(id, icon, label);
	registerTool(tool);

	// Slider settings for the ring distances (meters)
	const MIN = 25;
	const MAX = 2000;
	const STEP = 25;
	const DEFAULT_RINGS = [100, 300, 600];
	const ringKeys = ["red", "yellow", "green"];
	// Same colours as in the shader (setDistanceShader in threedee-layer.ts)
	const ringColors = ["#d91f29", "#fcd13b", "#24a147"];

	// Layer (ThreedeeLayer) the analysis runs on, set via tool settings: { "layerId": "3" }
	let layerId: string | undefined;
	let layer: any;
	let center: Writable<unknown> | undefined;
	let active: Writable<boolean> | undefined;
	let hasRings = false;
	let rings: Array<number> = [...DEFAULT_RINGS];
	let warned = "";

	tool.settings.subscribe((settings: any) => {
		layerId = settings?.layerId;
		tryResolveLayer();
	});

	map.ready.subscribe((r: any) => {
		if (r) tryResolveLayer();
	});

	// The layer is often created after this tool, so keep trying until it is there.
	const poll = setInterval(() => {
		if (layer) {
			clearInterval(poll);
			return;
		}
		tryResolveLayer();
	}, 500);
	onDestroy(() => clearInterval(poll));

	// Push every slider change to the layer (also keeps the rings in ascending order)
	$: if (layer) applyRings(rings);

	function warnOnce(message: string, extra?: unknown): void {
		if (warned === message) return;
		warned = message;
		console.warn(`[analysis] ${message}`, extra ?? "");
	}

	function tryResolveLayer(): void {
		if (layer) return;
		if (!layerId) {
			warnOnce(
				'geen layerId in de tool settings. Zet in de config: "settings": { "alias": "Analyse", "layerId": "3" }'
			);
			return;
		}

		let found: any;
		try {
			found = map.getLayerById(layerId);
		} catch (e) {
			warnOnce(`getLayerById("${layerId}") gaf een fout`, e);
			return;
		}
		if (!found) {
			warnOnce(`laag met id "${layerId}" bestaat (nog) niet`);
			return;
		}
		if (!found.selectedCenter || !found.distanceActive) {
			warnOnce(
				"de laag mist selectedCenter/distanceActive: is threedee-layer.ts aangepast?",
				found
			);
			return;
		}

		if (found.distanceRings) {
			hasRings = true;
			rings = [...get(found.distanceRings)];
		} else {
			warnOnce(
				"de laag mist distanceRings: sliders werken pas na de aanpassing in threedee-layer.ts"
			);
		}
		center = found.selectedCenter;
		active = found.distanceActive;
		layer = found;
	}

	function applyRings(values: Array<number>): void {
		const red = Number(values[0]);
		const yellow = Math.max(Number(values[1]), red);
		const green = Math.max(Number(values[2]), yellow);

		// Snap sliders back when the order was broken (e.g. yellow dragged below red).
		// Assigning here does not re-run this statement, so the layer is updated below in the same pass.
		if (rings[0] !== red || rings[1] !== yellow || rings[2] !== green) {
			rings = [red, yellow, green];
		}
		layer?.distanceRings?.set([red, yellow, green]);
	}

	function resetRings(): void {
		rings = [...DEFAULT_RINGS];
	}

	function toggleDistance(): void {
		if (!layer) return;
		layer.toggleDistance(!$active);
	}
</script>

{#if $selectedTool === tool}
	<div class="wrapper">
		<div class="section">
			<div class="label-02">{$_("tools.analysis.distance.title")}</div>
			<p class="label-01 hint">{$_("tools.analysis.distance.hint")}</p>

			{#if !layer}
				<InlineNotification
					lowContrast
					hideCloseButton
					kind="info"
					title={$_("tools.analysis.noLayer")}
				/>
			{:else}
				<p class="label-01 status">
					{$center
						? $_("tools.analysis.distance.centerSet")
						: $_("tools.analysis.distance.noCenter")}
				</p>
				<Button
					size="small"
					kind={$active ? "secondary" : "primary"}
					disabled={!$center && !$active}
					on:click={toggleDistance}
				>
					{$active ? $_("tools.analysis.distance.off") : $_("tools.analysis.distance.on")}
				</Button>

				{#if hasRings}
					<div class="rings">
						<div class="label-02">{$_("tools.analysis.distance.rings")}</div>
						{#each ringKeys as key, i}
							<div class="ring">
								<span class="swatch" style="background: {ringColors[i]}" />
								<div class="slider">
									<Slider
										labelText={$_(`tools.analysis.distance.${key}`)}
										min={MIN}
										max={MAX}
										step={STEP}
										bind:value={rings[i]}
									/>
								</div>
							</div>
						{/each}
						<div class="ring beyond">
							<span class="swatch" style="background: #0f33a6" />
							<span class="label-01">{$_("tools.analysis.distance.beyond")}</span>
						</div>
						<Button size="small" kind="ghost" on:click={resetRings}>
							{$_("tools.analysis.distance.reset")}
						</Button>
					</div>
				{/if}
			{/if}
		</div>
		<div class="divider" />
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

	.hint,
	.status {
		margin: 0;
		color: var(--cds-text-secondary);
	}

	.rings {
		width: 100%;
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		row-gap: var(--cds-spacing-03);
		padding-top: var(--cds-spacing-05);
	}

	.ring {
		width: 100%;
		display: flex;
		align-items: flex-start;
		column-gap: var(--cds-spacing-03);
	}

	.ring.beyond {
		align-items: center;
	}

	.swatch {
		flex: 0 0 auto;
		width: 0.75rem;
		height: 0.75rem;
		margin-top: 0.125rem;
		border-radius: 50%;
	}

	.ring.beyond .swatch {
		margin-top: 0;
	}

	.slider {
		flex: 1 1 auto;
		min-width: 0;
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
