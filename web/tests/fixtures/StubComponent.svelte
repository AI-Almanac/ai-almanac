<!--
	A real, inert Svelte component for `vi.mock`ing children out of a test.

	Mocking with `() => ({ default: {} })` only appears to work: the moment the
	component is actually instantiated Svelte throws "default is not a function".
	When that happens inside an effect it surfaces as an *unhandled* error rather
	than a test failure, so assertions can still pass while the component tree is
	broken — Vitest flags these as "unhandled errors ... might cause false positive
	tests".

	Declares no props deliberately. Svelte ignores props a component doesn't
	destructure, so this stands in for anything; calling $props() and discarding the
	result instead triggers a state_referenced_locally warning.
-->
<div data-testid="stub-component"></div>
