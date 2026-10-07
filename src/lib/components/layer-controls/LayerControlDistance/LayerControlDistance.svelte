<script lang="ts">
	import type { Writable } from "svelte/store";
	import { Button } from "carbon-components-svelte";

	export let center: Writable<unknown | undefined>;
	export let active: Writable<boolean>;
	export let onToggle: (on: boolean) => void;
</script>

<div class="wrapper">
	<p>middelpunt: {$center ? "ja" : "nee"}</p>
	<Button size="small" kind={$active ? "secondary" : "primary"} on:click={() => onToggle(!$active)}>
		{$active ? "Afstandsanalyse uit" : "Afstandsanalyse aan"}
	</Button>
</div>

<style>
	.wrapper {
		margin: 10px 0 15px;
	}
</style>
